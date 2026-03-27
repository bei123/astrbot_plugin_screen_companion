#!/usr/bin/env sh
set -eu

SERVICE_NAME="astrbot-screen-bridge"
OUTPUT_DIR=""
INTERVAL="5"
QUALITY="85"
HISTORY_LIMIT="120"
DISPLAY_VALUE="${DISPLAY:-:0}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --service-name)
            SERVICE_NAME="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --quality)
            QUALITY="$2"
            shift 2
            ;;
        --history-limit)
            HISTORY_LIMIT="$2"
            shift 2
            ;;
        --display)
            DISPLAY_VALUE="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
LAUNCHER_PATH="$PLUGIN_ROOT/scripts/start_docker_screenshot_bridge_linux.sh"

if [ ! -f "$LAUNCHER_PATH" ]; then
    echo "Launcher not found: $LAUNCHER_PATH" >&2
    exit 1
fi

if [ -z "$OUTPUT_DIR" ]; then
    OUTPUT_DIR="$PLUGIN_ROOT/docker_screenshots"
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required for this installer." >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
mkdir -p "$HOME/.config/systemd/user"

SERVICE_PATH="$HOME/.config/systemd/user/$SERVICE_NAME.service"

cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=AstrBot Docker screenshot bridge
After=graphical-session.target
Wants=graphical-session.target

[Service]
Type=simple
Environment=SCREENSHOT_OUTPUT_DIR=$OUTPUT_DIR
Environment=SCREENSHOT_INTERVAL=$INTERVAL
Environment=SCREENSHOT_QUALITY=$QUALITY
Environment=SCREENSHOT_HISTORY_LIMIT=$HISTORY_LIMIT
Environment=DISPLAY=$DISPLAY_VALUE
ExecStart=/bin/sh $LAUNCHER_PATH
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME.service"

echo "Installed user service: $SERVICE_PATH"
echo "Output directory: $OUTPUT_DIR"
echo "Service name: $SERVICE_NAME.service"
echo "Useful commands:"
echo "  systemctl --user status $SERVICE_NAME.service"
echo "  journalctl --user -u $SERVICE_NAME.service -f"
