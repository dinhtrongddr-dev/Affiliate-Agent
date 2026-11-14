@echo off
rem Start ChatGPT2API and show whether it runs (see start-chatgpt2api.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-chatgpt2api.ps1" %*
