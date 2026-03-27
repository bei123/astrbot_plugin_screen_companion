#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$TaskName = "AstrBot Screen Bridge",
    [string]$OutputDir = "",
    [double]$Interval = 5,
    [int]$Quality = 85,
    [int]$HistoryLimit = 120,
    [string]$PythonCommand = ""
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pluginRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)
$launcherPath = Join-Path $pluginRoot "scripts\start_docker_screenshot_bridge_windows.bat"

if (-not (Test-Path -LiteralPath $launcherPath)) {
    throw "未找到启动脚本: $launcherPath"
}

if (-not $OutputDir) {
    $OutputDir = Join-Path $pluginRoot "docker_screenshots"
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$commandParts = @()
$commandParts += "set `"SCREENSHOT_OUTPUT_DIR=$OutputDir`""
$commandParts += "set `"SCREENSHOT_INTERVAL=$Interval`""
$commandParts += "set `"SCREENSHOT_QUALITY=$Quality`""
$commandParts += "set `"SCREENSHOT_HISTORY_LIMIT=$HistoryLimit`""
if ($PythonCommand) {
    $commandParts += "set `"PYTHON_CMD=$PythonCommand`""
}
$commandParts += "call `"$launcherPath`""
$command = ($commandParts -join " && ")

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $command"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Start AstrBot Docker screenshot bridge at user logon." `
    -Force | Out-Null

Write-Host "已注册计划任务: $TaskName"
Write-Host "输出目录: $OutputDir"
Write-Host "启动脚本: $launcherPath"
Write-Host "你可以注销并重新登录，或在任务计划程序里手动运行一次该任务。"
