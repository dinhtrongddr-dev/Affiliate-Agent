@echo off
setlocal
rem One-click: web UI + background worker + inbox watcher, opens the browser.
set "ROOT=%~dp0.."
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%\services"
set "PYTHONUTF8=1"
set "TEMP=%ROOT%\cache\tmp"
set "TMP=%ROOT%\cache\tmp"
set "PLAYWRIGHT_BROWSERS_PATH=%ROOT%\cache\playwright"
if not exist "%TEMP%" mkdir "%TEMP%"
".venv\Scripts\python.exe" -m agent_core.cli ui %*
endlocal
