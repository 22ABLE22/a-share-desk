# Start a-share-desk uvicorn hidden (no console window)
param(
  [string]$Root = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$ErrorActionPreference = 'Stop'
$py = Join-Path $Root '.venv\Scripts\python.exe'
$backend = Join-Path $Root 'backend'
if (-not (Test-Path $py)) { Write-Host "[FAIL] missing $py"; exit 1 }
if (-not (Test-Path (Join-Path $backend 'main.py'))) { Write-Host "[FAIL] missing main.py"; exit 1 }
Start-Process -FilePath $py -ArgumentList @('-m','uvicorn','main:app','--host','127.0.0.1','--port','8765') -WorkingDirectory $backend -WindowStyle Hidden
Write-Host '[OK] process launched (hidden)'
exit 0
