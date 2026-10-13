@echo off
setlocal
title Gupiao Start
set "PORT=8765"
set "ROOT=%~dp0"
set "LOG=%ROOT%backend\uvicorn.log"

if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo [FAIL] Missing venv
  pause
  exit /b 1
)

echo Stopping old instance...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%stop.ps1" >nul 2>&1

echo Starting service in background (no window)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start.ps1" -Root "%ROOT%."
if errorlevel 1 (
  echo [FAIL] start.ps1 failed
  pause
  exit /b 1
)

ping -n 5 127.0.0.1 >nul
powershell -NoProfile -Command "try{(Invoke-WebRequest 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 3)|Out-Null; exit 0}catch{exit 1}"
if not errorlevel 1 goto OPEN
ping -n 5 127.0.0.1 >nul
powershell -NoProfile -Command "try{(Invoke-WebRequest 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 3)|Out-Null; exit 0}catch{exit 1}"
if not errorlevel 1 goto OPEN
ping -n 5 127.0.0.1 >nul
powershell -NoProfile -Command "try{(Invoke-WebRequest 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 3)|Out-Null; exit 0}catch{exit 1}"
if not errorlevel 1 goto OPEN

echo [FAIL] Not ready. Log:
if exist "%LOG%" type "%LOG%"
pause
exit /b 1

:OPEN
echo [OK] Running silently. http://127.0.0.1:8765/
start "" "http://127.0.0.1:8765/"
exit /b 0
