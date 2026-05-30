#!/usr/bin/env python3
# pylint: disable=broad-exception-caught
"""
OBS Capture Monitor

Monitors macOS screen/display capture sources in OBS Studio and restarts a
capture only after it has remained visually unchanged for a conservative
number of checks.

Inspired by:
https://github.com/yayuanli/OBS_Restart_Capture_Stuck_Source
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import signal
import socket
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def load_config_file() -> None:
    config_path = Path(
        os.environ.get(
            "OBS_MONITOR_CONFIG",
            str(Path.home() / "Library" / "Application Support" / "obs_capture_monitor" / "config.env"),
        )
    ).expanduser()
    if not config_path.exists():
        return

    for line in config_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_config_file()


def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_csv(name: str, default: str = "") -> list[str]:
    value = os.environ.get(name, default)
    return [part.strip() for part in value.split(",") if part.strip()]


# OBS > Tools > WebSocket Server Settings > Show Connect Info > Server Password
PASSWORD = env_str("OBS_PASSWORD", "")
HOST = env_str("OBS_HOST", "192.168.1.166")
PORT = env_int("OBS_PORT", 4455)

# Conservative defaults avoid the old tight restart loop on static captures.
CHECK_INTERVAL = env_float("OBS_CHECK_INTERVAL", 30.0)
STUCK_THRESHOLD = env_int("OBS_STUCK_THRESHOLD", 6)
RESTART_COOLDOWN = env_float("OBS_RESTART_COOLDOWN", 600.0)
POLL_INTERVAL = env_float("OBS_POLL_INTERVAL", 30.0)
REQUEST_TIMEOUT = env_float("OBS_REQUEST_TIMEOUT", 8.0)

# Optional explicit allow-list. Example: OBS_SOURCE_NAMES="Main Display,Camera"
SOURCE_NAMES = env_csv("OBS_SOURCE_NAMES")

# Auto-discovery only watches display/screen-style captures unless a source name
# allow-list is supplied. This avoids restarting static media, text, browser, or
# window captures by accident.
CAPTURE_KIND_KEYWORDS = tuple(
    part.lower() for part in env_csv("OBS_CAPTURE_KIND_KEYWORDS", "display,screen,macos")
)

SCREENSHOT_WIDTH = env_int("OBS_SCREENSHOT_WIDTH", 320)
SCREENSHOT_HEIGHT = env_int("OBS_SCREENSHOT_HEIGHT", 180)
LOG_DIR = Path(env_str("OBS_MONITOR_LOG_DIR", str(Path.home() / "Library" / "Logs" / "obs_capture_monitor"))).expanduser()
LOG_PATH = LOG_DIR / "obs_capture_monitor.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,
)
LOGGER = logging.getLogger("obs_capture_monitor")
SHUTTING_DOWN = False


class SimpleWebSocket:
    """Minimal RFC 6455 text WebSocket client for OBS's ws:// endpoint."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def connect(self) -> "SimpleWebSocket":
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._read_headers()
        if not response.startswith("HTTP/1.1 101") and not response.startswith("HTTP/1.0 101"):
            raise ConnectionError(f"OBS WebSocket handshake failed: {response.splitlines()[0]}")
        return self

    def _read_headers(self) -> str:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self._read_exact(1)
            data.extend(chunk)
            if len(data) > 65536:
                raise ConnectionError("WebSocket handshake response is too large")
        return data.decode("iso-8859-1", errors="replace")

    def _read_exact(self, size: int) -> bytes:
        if self.sock is None:
            raise ConnectionError("WebSocket is closed")
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("WebSocket connection closed")
            data.extend(chunk)
        return bytes(data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.sock is None:
            raise ConnectionError("WebSocket is closed")

        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length <= 125:
            header.append(0x80 | length)
        elif length <= 65535:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))

        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self) -> str:
        fragments: list[bytes] = []
        while True:
            first, second = self._read_exact(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]

            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x8:
                raise ConnectionError("WebSocket closed by peer")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode not in (0x0, 0x1):
                continue

            fragments.append(payload)
            if fin:
                return b"".join(fragments).decode("utf-8")

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None


class OBSWebSocketClient:
    """Small OBS WebSocket v5 client for the requests this service needs."""

    def __init__(self, host: str, port: int, password: str, timeout: float) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.ws: SimpleWebSocket | None = None
        self.request_id = 0

    def connect(self) -> bool:
        try:
            LOGGER.info("Connecting to OBS WebSocket at %s:%s", self.host, self.port)
            self.ws = SimpleWebSocket(self.host, self.port, self.timeout).connect()

            hello = json.loads(self.ws.recv())
            if hello.get("op") != 0:
                raise RuntimeError(f"Expected OBS hello, got: {hello}")

            auth_data = hello.get("d", {}).get("authentication")
            identify: dict[str, Any] = {
                "op": 1,
                "d": {
                    "rpcVersion": 1,
                    "eventSubscriptions": 33 | (1 << 14),
                },
            }

            if auth_data and self.password:
                secret = base64.b64encode(
                    hashlib.sha256((self.password + auth_data["salt"]).encode("utf-8")).digest()
                )
                identify["d"]["authentication"] = base64.b64encode(
                    hashlib.sha256(secret + auth_data["challenge"].encode("utf-8")).digest()
                ).decode("utf-8")

            self.ws.send(json.dumps(identify))
            identified = json.loads(self.ws.recv())
            if identified.get("op") != 2:
                raise RuntimeError(f"OBS identify failed: {identified}")

            version = self.request("GetVersion")
            obs_version = version.get("responseData", {}).get("obsVersion", "unknown")
            LOGGER.info("Connected to OBS Studio %s", obs_version)
            return True
        except Exception as exc:
            LOGGER.info("OBS WebSocket unavailable: %s", exc)
            self.close()
            return False

    def close(self) -> None:
        if self.ws is None:
            return
        try:
            self.ws.close()
        except Exception:
            pass
        finally:
            self.ws = None

    def request(self, request_type: str, request_data: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("OBS WebSocket is not connected")

        self.request_id += 1
        request_id = f"obs_capture_monitor_{self.request_id}"
        payload = {
            "op": 6,
            "d": {
                "requestId": request_id,
                "requestType": request_type,
                "requestData": request_data or {},
            },
        }
        self.ws.send(json.dumps(payload))

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                message = self.ws.recv()
            except socket.timeout as exc:
                raise TimeoutError(f"Timed out waiting for {request_type}") from exc

            response = json.loads(message)
            if response.get("op") != 7:
                continue

            data = response.get("d", {})
            if data.get("requestId") != request_id:
                continue

            status = data.get("requestStatus", {})
            if status and not status.get("result", True):
                comment = status.get("comment") or status
                raise RuntimeError(f"OBS request {request_type} failed: {comment}")
            return data

        raise TimeoutError(f"Timed out waiting for {request_type}")


@dataclass
class SourceState:
    prev_hash: str | None = None
    identical_count: int = 0
    last_restart: float = 0.0


class CaptureMonitor:
    """Monitors selected OBS capture sources with one shared connection."""

    def __init__(self, source_names: list[str]) -> None:
        self.source_names = source_names
        self.client = OBSWebSocketClient(HOST, PORT, PASSWORD, REQUEST_TIMEOUT)
        self.states = {name: SourceState() for name in source_names}
        self.running = True

    def connect(self) -> bool:
        return self.client.connect()

    def stop(self) -> None:
        self.running = False
        self.client.close()

    def screenshot_hash(self, source_name: str) -> str | None:
        response = self.client.request(
            "GetSourceScreenshot",
            {
                "sourceName": source_name,
                "imageFormat": "png",
                "imageWidth": SCREENSHOT_WIDTH,
                "imageHeight": SCREENSHOT_HEIGHT,
                "imageCompressionQuality": 50,
            },
        )
        image_data = response.get("responseData", {}).get("imageData")
        if not image_data:
            return None

        encoded = image_data.split(",", 1)[-1]
        return hashlib.md5(base64.b64decode(encoded)).hexdigest()

    def restart_capture(self, source_name: str) -> bool:
        state = self.states[source_name]
        now = time.time()
        if now - state.last_restart < RESTART_COOLDOWN:
            LOGGER.info("[%s] Restart skipped; cooldown is active", source_name)
            return False

        settings_response = self.client.request("GetInputSettings", {"inputName": source_name})
        current_settings = settings_response.get("responseData", {}).get("inputSettings", {})

        if "type" not in current_settings:
            LOGGER.warning("[%s] No supported capture toggle found in OBS settings", source_name)
            state.last_restart = now
            return False

        original_type = current_settings["type"]
        if isinstance(original_type, bool):
            temporary_type = not original_type
        elif isinstance(original_type, int):
            temporary_type = 1 if original_type == 0 else 0
        elif isinstance(original_type, str) and original_type in {"display", "window"}:
            temporary_type = "window" if original_type == "display" else "display"
        else:
            LOGGER.warning("[%s] Unsupported capture type value: %r", source_name, original_type)
            state.last_restart = now
            return False

        LOGGER.warning("[%s] Restarting capture after %s unchanged checks", source_name, STUCK_THRESHOLD)
        self.client.request(
            "SetInputSettings",
            {"inputName": source_name, "inputSettings": {**current_settings, "type": temporary_type}},
        )
        time.sleep(0.5)
        self.client.request(
            "SetInputSettings",
            {"inputName": source_name, "inputSettings": {**current_settings, "type": original_type}},
        )
        state.last_restart = now
        LOGGER.warning("[%s] Restart completed", source_name)
        return True

    def check_sources(self) -> None:
        for source_name in self.source_names:
            state = self.states[source_name]
            current_hash = self.screenshot_hash(source_name)
            if current_hash is None:
                LOGGER.warning("[%s] Could not get a source screenshot", source_name)
                continue

            if state.prev_hash is not None and current_hash == state.prev_hash:
                state.identical_count += 1
                LOGGER.info(
                    "[%s] Unchanged frame %s/%s",
                    source_name,
                    state.identical_count,
                    STUCK_THRESHOLD,
                )
                if state.identical_count >= STUCK_THRESHOLD:
                    self.restart_capture(source_name)
                    state.identical_count = 0
                    state.prev_hash = self.screenshot_hash(source_name) or current_hash
            else:
                if state.identical_count:
                    LOGGER.info("[%s] Frame changed; source looks active", source_name)
                state.identical_count = 0
                state.prev_hash = current_hash

    async def monitor(self) -> None:
        LOGGER.info(
            "Monitoring %s source(s): %s",
            len(self.source_names),
            ", ".join(self.source_names),
        )
        while self.running and not SHUTTING_DOWN:
            try:
                await asyncio.to_thread(self.check_sources)
            except Exception as exc:
                LOGGER.warning("Monitor lost OBS connection: %s", exc)
                break
            await asyncio.sleep(CHECK_INTERVAL)
        self.stop()


def is_obs_running(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def fetch_obs_inputs() -> list[dict[str, Any]]:
    client = OBSWebSocketClient(HOST, PORT, PASSWORD, REQUEST_TIMEOUT)
    if not client.connect():
        return []
    try:
        response = client.request("GetInputList")
        return response.get("responseData", {}).get("inputs", [])
    except Exception as exc:
        LOGGER.warning("Failed to fetch OBS inputs: %s", exc)
        return []
    finally:
        client.close()


def is_monitorable_capture(input_info: dict[str, Any]) -> bool:
    kind = str(input_info.get("inputKind", "")).lower()
    name = str(input_info.get("inputName", "")).lower()
    combined = f"{kind} {name}"

    blocked = ("audio", "browser", "color", "ffmpeg", "image", "media", "scene", "text")
    if any(block in kind for block in blocked):
        return False

    return "capture" in combined and any(keyword in combined for keyword in CAPTURE_KIND_KEYWORDS)


def select_source_names(inputs: list[dict[str, Any]]) -> list[str]:
    available = {str(item.get("inputName", "")): item for item in inputs if item.get("inputName")}
    if SOURCE_NAMES:
        missing = [name for name in SOURCE_NAMES if name not in available]
        if missing:
            LOGGER.warning("Configured OBS_SOURCE_NAMES not found: %s", ", ".join(missing))
        return [name for name in SOURCE_NAMES if name in available]

    selected = [name for name, info in available.items() if is_monitorable_capture(info)]
    skipped = [name for name in available if name not in selected]
    LOGGER.info("Auto-selected capture sources: %s", ", ".join(selected) or "none")
    if skipped:
        LOGGER.info("Skipped non-display capture inputs: %s", ", ".join(skipped))
    return selected


def handle_signal(signum: int, _frame: Any) -> None:
    global SHUTTING_DOWN
    LOGGER.info("Received signal %s; shutting down", signum)
    SHUTTING_DOWN = True


async def background_obs_monitor() -> None:
    LOGGER.info("OBS Capture Monitor service started")
    LOGGER.info("Logging to %s", LOG_PATH)
    LOGGER.info(
        "Config: host=%s port=%s interval=%ss threshold=%s cooldown=%ss poll=%ss",
        HOST,
        PORT,
        CHECK_INTERVAL,
        STUCK_THRESHOLD,
        RESTART_COOLDOWN,
        POLL_INTERVAL,
    )

    while not SHUTTING_DOWN:
        if not is_obs_running(HOST, PORT):
            LOGGER.info("%s OBS WebSocket not reachable; polling", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            await asyncio.sleep(POLL_INTERVAL)
            continue

        inputs = fetch_obs_inputs()
        source_names = select_source_names(inputs)
        if not source_names:
            LOGGER.info("No monitorable OBS capture sources found; polling")
            await asyncio.sleep(POLL_INTERVAL)
            continue

        monitor = CaptureMonitor(source_names)
        if monitor.connect():
            await monitor.monitor()
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    asyncio.run(background_obs_monitor())
