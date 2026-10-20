# Start FleetOps on a fresh, fully populated DEMO database (your normal data is untouched).
#
#   .\samples\start-demo.ps1                 # reseed and serve at http://localhost:8001
#   .\samples\start-demo.ps1 -Port 8002      # another port
#   .\samples\start-demo.ps1 -Users          # also create demo staff accounts (admin, supervisor1, ...)
#   .\samples\start-demo.ps1 -Keep           # keep the demo database as it is (don't reseed)
#
# Keys come from .env, as usual. Seeding itself makes no API calls: the sample runs use offline
# straight-line routes. Runs you plan yourself use whatever FAKE_ROUTES says in .env.

param(
    [int]$Port = 8001,
    [switch]$Users,
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$env:DB_PATH = "fleet-demo.db"

# load .env, but never override values that are already set in this window
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)$') {
            $name = $matches[1].Trim()
            if (-not (Test-Path "env:$name")) { Set-Item "env:$name" $matches[2].Trim() }
        }
    }
}

if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Error "No venv found. Run: python -m venv venv; venv\Scripts\pip install -r requirements.txt"
}

if (-not $Keep) {
    Remove-Item "fleet-demo.db*" -ErrorAction SilentlyContinue
    $seedArgs = @("samples\seed_demo.py", "--reset")
    if ($Users) { $seedArgs += "--users" }
    & venv\Scripts\python.exe @seedArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "Demo is at http://localhost:$Port   (Ctrl+C to stop)" -ForegroundColor Cyan
& venv\Scripts\python.exe -m uvicorn app.main:app --port $Port
