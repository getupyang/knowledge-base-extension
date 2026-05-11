$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 "$ScriptDir\scripts\memai_windows.py" onboard
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  & python "$ScriptDir\scripts\memai_windows.py" onboard
} else {
  Write-Host "未找到 Python。请先安装 Python 3.9+，并勾选 Add python.exe to PATH。"
  exit 1
}
exit $LASTEXITCODE
