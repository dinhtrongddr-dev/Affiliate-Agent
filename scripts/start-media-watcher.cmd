@echo off
setlocal
rem Project root = parent of this scripts folder
set "ROOT=%~dp0.."
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%\services"
set "TEMP=%ROOT%\cache\tmp"
set "TMP=%ROOT%\cache\tmp"
set "PLAYWRIGHT_BROWSERS_PATH=%ROOT%\cache\playwright"
if not exist "%TEMP%" mkdir "%TEMP%"
".venv\Scripts\python.exe" -m agent_core.cli watch %*
endlocal
