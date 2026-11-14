@echo off
setlocal
rem One-click: web UI + background worker + inbox watcher, opens the browser.
set "ROOT=%~dp0.."
cd /d "%ROOT%"
if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua cai xong: thieu .venv\Scripts\python.exe
    echo Chay lai lenh cai dat:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
    pause
    exit /b 1
)
set "PYTHONPATH=%ROOT%\services"
set "PYTHONUTF8=1"
set "TEMP=%ROOT%\cache\tmp"
set "TMP=%ROOT%\cache\tmp"
set "PLAYWRIGHT_BROWSERS_PATH=%ROOT%\cache\playwright"
if not exist "%TEMP%" mkdir "%TEMP%"
".venv\Scripts\python.exe" -c "import agent_core.web.app" 2>nul
if errorlevel 1 (
    echo [LOI] Thu vien chua du hoac bi loi. Chi tiet:
    ".venv\Scripts\python.exe" -c "import agent_core.web.app"
    echo.
    echo Sua bang:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
    pause
    exit /b 1
)
echo Dang mo Affiliate Agent tai http://127.0.0.1:8765 ... (dong cua so nay = tat app)
".venv\Scripts\python.exe" -m agent_core.cli ui %*
if errorlevel 1 (
    echo.
    echo [LOI] App dung bat thuong. Xem logs\agent.log
    pause
)
endlocal
