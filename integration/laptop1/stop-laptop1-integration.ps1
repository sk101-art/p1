# ==============================================================================
# Auto-SRE Platform - laptop1 Phase1 <-> frozen Phase2 Integration
# Stop script: gracefully stops the durable local integration service.
# ==============================================================================

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition
$STATE = Join-Path $ROOT "runtime\state"
$pidFile = Join-Path $STATE "integration.pid"

Write-Host "=== [laptop1 integration] Stopping service ===" -ForegroundColor Cyan

if (-not (Test-Path $pidFile)) {
    Write-Host "[-] No PID file found at $pidFile. Service may not be running." -ForegroundColor Yellow
    exit 0
}

$pid = (Get-Content $pidFile -Raw).Trim()
if (-not $pid) {
    Write-Host "[-] PID file empty." -ForegroundColor Yellow
    Remove-Item $pidFile -Force
    exit 0
}

try {
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) {
        # Try graceful termination first.
        $proc.CloseMainWindow() | Out-Null
        if (-not $proc.WaitForExit(5000)) {
            Stop-Process -Id $pid -Force
        }
        Write-Host "[+] Stopped integration service (PID $pid)." -ForegroundColor Green
    } else {
        Write-Host "[-] Process $pid not running." -ForegroundColor Yellow
    }
} catch {
    Write-Host "[!] Error stopping process ${pid}: $_" -ForegroundColor Red
} finally {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
