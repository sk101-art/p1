# ==============================================================================
# Auto-SRE Platform - laptop1 Phase1 <-> frozen Phase2 Integration
# Verify script: checks frozen Phase 2 integrity + integration service health.
# ==============================================================================

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ROOT

$PY = Join-Path $ROOT ".venv-phase2\Scripts\python.exe"

Write-Host "=== [laptop1 integration] Verification ===" -ForegroundColor Cyan

# 1. Frozen Phase 2 integrity
Write-Host "`n[1/3] Verifying frozen Phase 2 files..." -ForegroundColor Yellow
& $PY integration/verify_frozen_phase2.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[-] Frozen Phase 2 verification FAILED." -ForegroundColor Red
    exit 1
}
Write-Host "[+] Frozen Phase 2 files intact." -ForegroundColor Green

# 2. Integration service health
Write-Host "`n[2/3] Checking integration service health (127.0.0.1:8102)..." -ForegroundColor Yellow
try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8102/health" -TimeoutSec 5
    Write-Host "[+] Health: $($resp | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "[!] Integration service not reachable on 127.0.0.1:8102. Start it with run-laptop1-integration.ps1." -ForegroundColor Yellow
}

# 3. SQLite state store presence
Write-Host "`n[3/3] Checking durable state store..." -ForegroundColor Yellow
$db = Join-Path $ROOT "runtime\state\laptop1_pipeline.db"
if (Test-Path $db) {
    Write-Host "[+] State store present: $db" -ForegroundColor Green
} else {
    Write-Host "[!] State store not yet created (will be created on first run)." -ForegroundColor Yellow
}

Write-Host "`n=== Verification complete ===" -ForegroundColor Cyan
