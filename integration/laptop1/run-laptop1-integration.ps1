# ==============================================================================
# Auto-SRE Platform - laptop1 Phase1 <-> frozen Phase2 Integration
# Startup script: launches the durable local integration service.
# Binds 127.0.0.1:8102 only (never 0.0.0.0).
# ==============================================================================

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ROOT

$VENV = Join-Path $ROOT ".venv-phase2"
$PY = Join-Path $VENV "Scripts\python.exe"
if (-not (Test-Path $PY)) {
    Write-Host "[-] .venv-phase2 not found. Run Phase 4 setup first (create venv + install requirements-integration.txt)." -ForegroundColor Red
    exit 1
}

$RUNTIME = Join-Path $ROOT "runtime"
$STATE = Join-Path $RUNTIME "state"
New-Item -ItemType Directory -Force -Path $STATE | Out-Null

Write-Host "=== [laptop1 integration] Starting durable orchestration service ===" -ForegroundColor Cyan
Write-Host "[*] Bind address: 127.0.0.1:8102 (loopback only)" -ForegroundColor Gray

# Launch the integration service in the background, logging to runtime/.
$logFile = Join-Path $RUNTIME "integration.log"
$proc = Start-Process -FilePath $PY -ArgumentList @(
    "-m", "integration.laptop1.main"
) -RedirectStandardOutput $logFile -RedirectStandardError (Join-Path $RUNTIME "integration.err") -NoNewWindow -PassThru

$pidFile = Join-Path $STATE "integration.pid"
Set-Content -Path $pidFile -Value $proc.Id
Write-Host "[+] Integration service started (PID $($proc.Id)). Log: $logFile" -ForegroundColor Green

# Give it a moment, then probe health.
Start-Sleep -Seconds 3
try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8102/health" -TimeoutSec 5
    Write-Host "[+] Health check OK: $($resp | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "[!] Health check not ready yet (service may still be warming). Check $logFile." -ForegroundColor Yellow
}
