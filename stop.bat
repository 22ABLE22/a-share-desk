@echo off
setlocal
title A-Share Desk Stop
echo Stopping a-share-desk...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
exit /b 0
