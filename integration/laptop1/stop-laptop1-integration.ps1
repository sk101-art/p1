$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PidFile = Join-Path $RepoRoot "runtime\state\integration.pid"

if (-not (Test-Path $PidFile)) {
    Write-Host "[-] No integration PID file; nothing to stop." -ForegroundColor Yellow
    exit 0
}

$RawProcessId = (Get-Content $PidFile -Raw).Trim()
$ProcessId = 0
if (-not [int]::TryParse($RawProcessId, [ref]$ProcessId) -or $ProcessId -le 0) {
    Write-Host "[-] Invalid PID file; refusing to stop any process." -ForegroundColor Red
    exit 1
}

$CimProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
if (-not $CimProcess) {
    Remove-Item $PidFile -Force
    Write-Host "[-] Recorded process is no longer running." -ForegroundColor Yellow
    exit 0
}
if ($CimProcess.CommandLine -notmatch "integration\.laptop1\.main") {
    Write-Host "[-] PID $ProcessId is not the Laptop 1 integration service; refusing to stop it." -ForegroundColor Red
    exit 1
}

Stop-Process -Id $ProcessId -ErrorAction Stop
$Deadline = (Get-Date).AddSeconds(5)
while ((Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) -and
       (Get-Date) -lt $Deadline) {
    Start-Sleep -Milliseconds 200
}
if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
    Stop-Process -Id $ProcessId -Force -ErrorAction Stop
}
Remove-Item $PidFile -Force
Write-Host "[+] Stopped Laptop 1 integration service PID $ProcessId." -ForegroundColor Green
