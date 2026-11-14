@echo off
rem Double-click: update AffiliateAgent from GitHub and restart it.
powershell -ExecutionPolicy Bypass -File "%~dp0update.ps1" %*
pause
