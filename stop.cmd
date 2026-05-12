@echo off
set SCRIPT_DIR=%~dp0
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%SCRIPT_DIR%scripts\memai_windows.py" stop
  exit /b %ERRORLEVEL%
)
where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%SCRIPT_DIR%scripts\memai_windows.py" stop
  exit /b %ERRORLEVEL%
)
echo 未找到 Python。请先安装 Python 3.9+，并勾选 Add python.exe to PATH。
exit /b 1
