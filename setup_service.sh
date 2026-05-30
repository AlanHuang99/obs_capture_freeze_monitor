#!/bin/bash

# OBS Capture Monitor Setup Script
# Installs and manages the monitor as a macOS user LaunchAgent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_FILE="com.user.obs-capture-monitor.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
SERVICE_NAME="com.user.obs-capture-monitor"
DOMAIN="gui/$(id -u)"
TARGET="$DOMAIN/$SERVICE_NAME"
LOG_DIR="$HOME/Library/Logs/obs_capture_monitor"
INSTALL_DIR="$HOME/Library/Application Support/obs_capture_monitor"
CONFIG_FILE="$INSTALL_DIR/config.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

setup_log_directory() {
    print_status "Setting up user log directory: $LOG_DIR"
    mkdir -p "$LOG_DIR"
    touch "$LOG_DIR/obs_capture_monitor.log" \
        "$LOG_DIR/service_output.log" \
        "$LOG_DIR/service_error.log"
}

install_runtime_files() {
    print_status "Installing runtime files: $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    cp "$SCRIPT_DIR/obs_capture_monitor_service.py" "$INSTALL_DIR/"
    chmod 755 "$INSTALL_DIR/obs_capture_monitor_service.py"

    if [ ! -f "$CONFIG_FILE" ]; then
        touch "$CONFIG_FILE"
        chmod 600 "$CONFIG_FILE"
    fi
}

bootout_service() {
    launchctl bootout "$DOMAIN" "$LAUNCH_AGENTS_DIR/$PLIST_FILE" 2>/dev/null \
        || launchctl unload "$LAUNCH_AGENTS_DIR/$PLIST_FILE" 2>/dev/null \
        || true
}

bootstrap_service() {
    launchctl bootstrap "$DOMAIN" "$LAUNCH_AGENTS_DIR/$PLIST_FILE" 2>/dev/null \
        || launchctl load "$LAUNCH_AGENTS_DIR/$PLIST_FILE"
}

install_service() {
    print_status "Installing OBS Capture Monitor service..."
    mkdir -p "$LAUNCH_AGENTS_DIR"
    install_runtime_files
    setup_log_directory
    bootout_service
    cp "$SCRIPT_DIR/$PLIST_FILE" "$LAUNCH_AGENTS_DIR/"
    chmod 644 "$LAUNCH_AGENTS_DIR/$PLIST_FILE"
    bootstrap_service
    print_status "Service installed and loaded."
    print_status "Logs: $LOG_DIR"
}

uninstall_service() {
    print_status "Uninstalling OBS Capture Monitor service..."
    bootout_service
    rm -f "$LAUNCH_AGENTS_DIR/$PLIST_FILE"
    print_status "Service uninstalled."
}

start_service() {
    if [ ! -f "$LAUNCH_AGENTS_DIR/$PLIST_FILE" ]; then
        print_warning "LaunchAgent is not installed; installing it now."
        install_service
        return
    fi
    print_status "Starting OBS Capture Monitor service..."
    install_runtime_files
    setup_log_directory
    bootout_service
    cp "$SCRIPT_DIR/$PLIST_FILE" "$LAUNCH_AGENTS_DIR/"
    chmod 644 "$LAUNCH_AGENTS_DIR/$PLIST_FILE"
    bootstrap_service
    print_status "Service started."
}

stop_service() {
    print_status "Stopping OBS Capture Monitor service..."
    bootout_service
    print_status "Service stopped."
}

restart_service() {
    print_status "Restarting OBS Capture Monitor service..."
    stop_service
    start_service
}

check_status() {
    print_status "Checking service status..."
    if launchctl print "$TARGET" >/dev/null 2>&1; then
        print_status "Service is loaded."
        launchctl print "$TARGET" | sed -n '1,80p'
    else
        print_warning "Service is not loaded."
    fi
}

show_logs() {
    print_status "Showing recent logs from $LOG_DIR"
    echo "=== Monitor Log ==="
    tail -n 30 "$LOG_DIR/obs_capture_monitor.log" 2>/dev/null || echo "No monitor log found"
    echo
    echo "=== LaunchAgent Standard Output ==="
    tail -n 20 "$LOG_DIR/service_output.log" 2>/dev/null || echo "No standard output log found"
    echo
    echo "=== LaunchAgent Standard Error ==="
    tail -n 20 "$LOG_DIR/service_error.log" 2>/dev/null || echo "No standard error log found"
}

show_help() {
    echo "OBS Capture Monitor Setup Script"
    echo
    echo "Usage: $0 [command]"
    echo
    echo "Commands:"
    echo "  install     Install and start the service"
    echo "  uninstall   Stop and remove the service"
    echo "  start       Start the installed service"
    echo "  stop        Stop the service"
    echo "  restart     Restart the service"
    echo "  status      Check service status"
    echo "  logs        Show recent logs"
    echo "  help        Show this help message"
    echo
    echo "The service starts automatically at login once installed."
}

case "${1:-help}" in
    install)
        install_service
        ;;
    uninstall)
        uninstall_service
        ;;
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    status)
        check_status
        ;;
    logs)
        show_logs
        ;;
    help|*)
        show_help
        ;;
esac
