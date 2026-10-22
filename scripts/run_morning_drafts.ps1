$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found at $python. Run 'uv sync' first."
}

Set-Location $projectRoot
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
$logDirectory = Join-Path $env:LOCALAPPDATA "LinkedInAIContentAgent\logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logFile = Join-Path $logDirectory "morning-drafts.log"

& $python manage.py generate_morning_drafts *>> $logFile
exit $LASTEXITCODE
