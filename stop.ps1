# Stop all local a-share-desk/uvicorn servers on 8765
$ErrorActionPreference = 'SilentlyContinue'

Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^python' -and (
    $_.CommandLine -match 'uvicorn' -or
    $_.CommandLine -match 'main:app'
  )
} | ForEach-Object {
  Write-Host "Kill PID $($_.ProcessId) $($_.Name)"
  Stop-Process -Id $_.ProcessId -Force
}

Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {
    $id = $_.OwningProcess
    if ($id) {
      Write-Host "Kill port PID $id"
      Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
  }

Start-Sleep -Seconds 1
$left = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($left) {
  Write-Host '[FAIL] Port 8765 still open'
  exit 1
}
Write-Host '[OK] Service stopped.'
exit 0
