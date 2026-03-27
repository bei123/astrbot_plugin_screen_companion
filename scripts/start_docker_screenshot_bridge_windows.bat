@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "DEFAULT_OUTPUT_DIR=%SCRIPT_DIR%..\docker_screenshots"

if not defined SCREENSHOT_OUTPUT_DIR set "SCREENSHOT_OUTPUT_DIR=%DEFAULT_OUTPUT_DIR%"
if not defined SCREENSHOT_INTERVAL set "SCREENSHOT_INTERVAL=5"
if not defined SCREENSHOT_QUALITY set "SCREENSHOT_QUALITY=85"
if not defined SCREENSHOT_HISTORY_LIMIT set "SCREENSHOT_HISTORY_LIMIT=120"

set "PYTHON_CMD="
where py.exe >nul 2>nul
if %ERRORLEVEL%==0 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python.exe >nul 2>nul
    if %ERRORLEVEL%==0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python not found. Please install Python 3 first.
    echo [ERROR] You can also run docker_screenshot_bridge.py manually after Python is installed.
    exit /b 1
)

if not exist "%SCREENSHOT_OUTPUT_DIR%" mkdir "%SCREENSHOT_OUTPUT_DIR%"

echo Writing screenshots to "%SCREENSHOT_OUTPUT_DIR%"
echo Interval=%SCREENSHOT_INTERVAL%s Quality=%SCREENSHOT_QUALITY% History=%SCREENSHOT_HISTORY_LIMIT%

call %PYTHON_CMD% "%SCRIPT_DIR%docker_screenshot_bridge.py" ^
    --output-dir "%SCREENSHOT_OUTPUT_DIR%" ^
    --interval "%SCREENSHOT_INTERVAL%" ^
    --quality "%SCREENSHOT_QUALITY%" ^
    --history-limit "%SCREENSHOT_HISTORY_LIMIT%" ^
    --verbose ^
    %*
