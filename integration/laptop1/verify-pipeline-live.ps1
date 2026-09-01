$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
$env:PYTHONPATH = $RepoRoot
$Phase2Python = Join-Path $RepoRoot ".venv-phase2\Scripts\python.exe"

if (-not (Test-Path $Phase2Python)) {
    Write-Host "[-] Missing interpreter: $Phase2Python" -ForegroundColor Red
    exit 1
}

Write-Host "`n[*] Running Live Pipeline Artifacts and Boundary Validation (pytest -m runtime)..." -ForegroundColor Yellow
& $Phase2Python -m pytest tests/integration/test_pipeline_validation.py -m runtime -v
$ExitCode = $LASTEXITCODE

if ($ExitCode -eq 0) {
    Write-Host "[+] Live pipeline artifact validation passed!" -ForegroundColor Green
} else {
    Write-Host "[-] Live pipeline artifact validation failed with exit code $ExitCode" -ForegroundColor Red
}
exit $ExitCode
