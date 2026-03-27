#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEFAULT_OUTPUT_DIR="$SCRIPT_DIR/../docker_screenshots"

: "${SCREENSHOT_OUTPUT_DIR:=$DEFAULT_OUTPUT_DIR}"
: "${SCREENSHOT_INTERVAL:=5}"
: "${SCREENSHOT_QUALITY:=85}"
: "${SCREENSHOT_HISTORY_LIMIT:=120}"

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
else
    echo "Python 3 is required to run docker_screenshot_bridge.py." >&2
    exit 1
fi

mkdir -p "$SCREENSHOT_OUTPUT_DIR"

echo "Writing screenshots to: $SCREENSHOT_OUTPUT_DIR"
echo "Interval=${SCREENSHOT_INTERVAL}s Quality=${SCREENSHOT_QUALITY} History=${SCREENSHOT_HISTORY_LIMIT}"

exec "$PYTHON_BIN" "$SCRIPT_DIR/docker_screenshot_bridge.py" \
    --output-dir "$SCREENSHOT_OUTPUT_DIR" \
    --interval "$SCREENSHOT_INTERVAL" \
    --quality "$SCREENSHOT_QUALITY" \
    --history-limit "$SCREENSHOT_HISTORY_LIMIT" \
    --verbose \
    "$@"
