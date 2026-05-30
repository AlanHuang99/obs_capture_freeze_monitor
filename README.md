# OBS Capture Freeze Monitor

A Python service that automatically monitors and restarts frozen capture sources in OBS Studio using the WebSocket API.

## Features

- **Automatic Detection**: Monitors macOS display/screen capture sources for frozen/stuck frames
- **Smart Restart**: Automatically restarts frozen sources by toggling capture settings
- **Background Service**: Runs continuously in the background on macOS
- **Multi-Source Support**: Monitors all sources simultaneously with a single WebSocket connection
- **Auto-Discovery**: Automatically detects when OBS starts/stops and adapts accordingly
- **macOS Integration**: Includes Launch Agent for automatic startup at login

## Requirements

- Python 3.12+
- OBS Studio with WebSocket plugin enabled - Tested with OBS Studio 31.0.3 (64 Bit) on Mac Sequoia 15.5 M3
- No third-party Python packages are required by the monitor service

## Installation

1. Clone the repository:

```bash
git clone <repository-url>
cd obs_capture_freeze_monitor
```

1. Configure OBS WebSocket:
   - In OBS: Tools → WebSocket Server Settings
      - Enable server on port 4455
      - Generate and copy the server password
2. Put machine-local settings in `~/Library/Application Support/obs_capture_monitor/config.env`, for example:

```bash
OBS_PASSWORD=your-websocket-password
```

## Usage

### Download

Release builds are published from tags and include `obs_capture_monitor_macos_service.zip` on the GitHub Releases page.

```bash
gh release download --pattern obs_capture_monitor_macos_service.zip
```

### Manual Run

Run the service manually:

```bash
python obs_capture_monitor_service.py
```

Logs are created under `~/Library/Logs/obs_capture_monitor`.

### Install as macOS Service (Recommended)

To automatically start the service at login, use the setup script:

```bash
# Make the setup script executable (if not already)
chmod +x setup_service.sh

# Install and start the service
./setup_service.sh install
```

The service will now start automatically when you log in to macOS.

The installer copies the runtime script to `~/Library/Application Support/obs_capture_monitor` before loading the LaunchAgent. This avoids macOS background-process privacy prompts around running scripts directly from `~/Documents`.

### Service Management

Use the setup script to manage the service:

```bash
# Check service status
./setup_service.sh status

# Start the service
./setup_service.sh start

# Stop the service
./setup_service.sh stop

# Restart the service
./setup_service.sh restart

# View recent logs
./setup_service.sh logs

# Uninstall the service
./setup_service.sh uninstall
```

### What the Service Does

The service will:

- Poll for OBS every 30 seconds when OBS WebSocket is unavailable
- Auto-discover display/screen capture sources when OBS is detected
- Skip non-display inputs such as audio, image, media, browser, text, and scene sources
- Restart a capture only after 6 unchanged checks at 30-second intervals
- Wait at least 10 minutes before restarting the same capture again
- Log activity to `~/Library/Logs/obs_capture_monitor/obs_capture_monitor.log`

## Configuration

Edit the LaunchAgent environment variables or script defaults to customize:

- `OBS_HOST`: OBS WebSocket host (default for this machine: `192.168.1.166`)
- `OBS_PORT`: OBS WebSocket port (default: 4455)
- `OBS_PASSWORD`: WebSocket password; keep it in `~/Library/Application Support/obs_capture_monitor/config.env` rather than committing it
- `OBS_SOURCE_NAMES`: Optional comma-separated source allow-list
- `OBS_CHECK_INTERVAL`: Seconds between screenshot checks (default: 30)
- `OBS_STUCK_THRESHOLD`: Unchanged checks before restart (default: 6)
- `OBS_RESTART_COOLDOWN`: Minimum seconds between restarts for the same source (default: 600)
- `OBS_CAPTURE_KIND_KEYWORDS`: Auto-discovery keywords (default: `display,screen,macos`)

## Log Files

When running as a service, logs are written to:

- Monitor log: `~/Library/Logs/obs_capture_monitor/obs_capture_monitor.log`
- Standard output: `~/Library/Logs/obs_capture_monitor/service_output.log`
- Error output: `~/Library/Logs/obs_capture_monitor/service_error.log`

## Troubleshooting

### Python Environment Issues

If the service does not start, make sure the LaunchAgent points at a valid Python binary. On this machine it is configured for `/opt/homebrew/bin/python3`.

1. Check your Python path: `which python3`
2. Update `com.user.obs-capture-monitor.plist` if needed
3. Reinstall the service with `./setup_service.sh install`

## Credits

Inspired by [yayuanli's OBS_Restart_Capture_Stuck_Source](https://github.com/yayuanli/OBS_Restart_Capture_Stuck_Source).

## License

MIT License - Feel free to use and modify! 
