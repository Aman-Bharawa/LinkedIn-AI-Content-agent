param(
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found at $python. Run 'uv sync' first."
}

Set-Location $projectRoot
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"

$preflight = @'
import sys
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from apps.core.models import MORNING_DRAFT_COUNT, MorningTopic, ScheduledDraftRun

connection.ensure_connection()
topics = list(MorningTopic.objects.order_by("topic").values_list("topic", flat=True))
owner_exists = get_user_model().objects.filter(is_active=True, is_superuser=True).exists()
today = timezone.localdate()
runs = list(ScheduledDraftRun.objects.filter(run_date=today).order_by("run_number"))
print(f"Database: {connection.settings_dict['NAME']} @ {connection.settings_dict['HOST']}")
print(f"Owner account configured: {'yes' if owner_exists else 'no'}")
print(f"Saved topic pool: {len(topics)} / 50")
for topic in topics:
    print(f"  - {topic}")
print(f"Today's draft slots: {len(runs)} / {MORNING_DRAFT_COUNT}")
for run in runs:
    topic = run.post.source_keyword if run.post_id else "(no topic assigned)"
    print(f"  - slot {run.run_number}: {run.status} · {topic}")

if not owner_exists:
    print("ERROR: Create an active Django superuser before running morning drafts.")
    sys.exit(2)
if len(topics) < MORNING_DRAFT_COUNT:
    print("ERROR: Add at least two different topics on the Morning topics page first.")
    sys.exit(2)
if len(runs) >= MORNING_DRAFT_COUNT and all(
    run.status == ScheduledDraftRun.Status.SUCCEEDED
    or (run.status == ScheduledDraftRun.Status.STARTED and run.post_id)
    for run in runs[:MORNING_DRAFT_COUNT]
):
    print("Today's two slots are already claimed or complete; the idempotent run will not add more drafts today.")
    sys.exit(3)
'@

Write-Host "Checking production settings, database, owner, and saved topics..." -ForegroundColor Cyan
& $python manage.py shell -c $preflight
if ($LASTEXITCODE -eq 3) {
    Write-Host "Today's morning run has already started or completed. Review today's saved drafts in the app." -ForegroundColor Yellow
    exit 0
}
if ($LASTEXITCODE -ne 0) {
    throw "Preflight failed. Fix the message above before generating drafts."
}

Write-Host ""
Write-Host "This runs today's two morning-draft slots now. It uses Gemini and the configured database." -ForegroundColor Yellow
Write-Host "It creates drafts for review only. It does not approve or publish them."
if (-not $Yes) {
    $answer = Read-Host "Type RUN to continue"
    if ($answer -cne "RUN") {
        Write-Host "Cancelled. No drafts were generated."
        exit 0
    }
}

Write-Host ""
Write-Host "Generating drafts..." -ForegroundColor Cyan
& $python manage.py generate_morning_drafts
$generationExitCode = $LASTEXITCODE

$summary = @'
from django.utils import timezone
from apps.core.models import ScheduledDraftRun

today = timezone.localdate()
for run in ScheduledDraftRun.objects.filter(run_date=today).select_related("post").order_by("run_number"):
    if run.post_id:
        print(f"slot {run.run_number}: {run.status} · {run.post.source_keyword} · draft #{run.post_id} · {run.post.status}")
    else:
        print(f"slot {run.run_number}: {run.status} · {run.error_message or 'no draft created'}")
'@
Write-Host ""
Write-Host "Today's draft results:" -ForegroundColor Cyan
& $python manage.py shell -c $summary
if ($LASTEXITCODE -ne 0) {
    throw "Draft run ended, but the result summary could not be loaded. Check the database connection."
}
if ($generationExitCode -ne 0) {
    throw "One or more drafts failed. Review the command output and Render app draft history."
}

Write-Host ""
Write-Host "Open https://linkedin-ai-content-agent-zpv4.onrender.com/ and review the new drafts." -ForegroundColor Green
