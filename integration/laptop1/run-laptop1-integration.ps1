$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv-phase2\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "[-] Missing .venv-phase2 at repository root." -ForegroundColor Red
    exit 1
}

$Runtime = Join-Path $RepoRoot "runtime"
$State = Join-Path $Runtime "state"
New-Item -ItemType Directory -Force -Path $State | Out-Null

& $Python "integration\verify_frozen_phase2.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$LogFile = Join-Path $Runtime "integration.log"
$ErrorFile = Join-Path $Runtime "integration.err"
$Process = Start-Process -FilePath $Python -ArgumentList @(
    "-m", "integration.laptop1.main"
) -WorkingDirectory $RepoRoot -RedirectStandardOutput $LogFile `
  -RedirectStandardError $ErrorFile -NoNewWindow -PassThru

$PidFile = Join-Path $State "integration.pid"
Set-Content -Path $PidFile -Value $Process.Id -Encoding ascii
Write-Host "[+] Integration service PID $($Process.Id); warming Phase 2..." -ForegroundColor Green

$Deadline = (Get-Date).AddSeconds(180)
do {
    if ($Process.HasExited) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Write-Host "[-] Service exited during startup. Inspect $ErrorFile" -ForegroundColor Red
        exit 1
    }
    try {
        $Ready = Invoke-RestMethod -Uri "http://127.0.0.1:8102/readyz" -TimeoutSec 5
        if ($Ready.status -eq "ready") {
            Write-Host "[+] Ready: $($Ready | ConvertTo-Json -Compress)" -ForegroundColor Green
            exit 0
        }
    } catch { }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $Deadline)

Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "[-] Readiness timeout. Inspect $ErrorFile" -ForegroundColor Red
exit 1

