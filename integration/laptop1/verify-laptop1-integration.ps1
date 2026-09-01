$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
$env:PYTHONPATH = $RepoRoot
$Phase1Python = Join-Path $RepoRoot ".venv-phase1\Scripts\python.exe"
$Phase2Python = Join-Path $RepoRoot ".venv-phase2\Scripts\python.exe"

foreach ($Python in @($Phase1Python, $Phase2Python)) {
    if (-not (Test-Path $Python)) {
        Write-Host "[-] Missing interpreter: $Python" -ForegroundColor Red
        exit 1
    }
}

function Invoke-Gate([string]$Name, [scriptblock]$Command) {
    Write-Host "`n[*] $Name" -ForegroundColor Yellow
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[-] $Name failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "[+] $Name passed" -ForegroundColor Green
}

Invoke-Gate "Frozen Phase 2 manifest" { & $Phase2Python "integration\verify_frozen_phase2.py" }
Invoke-Gate "Phase 1 tests" { & $Phase1Python -m pytest tests -m "not runtime" --ignore=tests/phase2 --ignore=tests/integration }
Invoke-Gate "Phase 2 tests" { & $Phase2Python -m pytest tests/phase2 -m "not runtime" }
Invoke-Gate "Laptop 1 integration tests" { & $Phase2Python -m pytest tests/integration -m "not runtime" }
Invoke-Gate "Schema drift" { & $Phase2Python "tests/verify_schema_drift.py" }
Invoke-Gate "Phase 1 dependencies" { & $Phase1Python -m pip check }
Invoke-Gate "Phase 2 dependencies" { & $Phase2Python -m pip check }
Invoke-Gate "Orchestration latency report" { & $Phase2Python "integration/laptop1/measure_latency.py" }
Write-Host "`n=== All Laptop 1 verification gates passed ===" -ForegroundColor Cyan

