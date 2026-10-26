param(
    [switch]$Yes,
    [string]$Username
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found at $python. Run 'uv sync' first."
}

Set-Location $projectRoot
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
$env:APP_MORNING_USERNAME = $Username

$preflight = @'
import sys
import os
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from apps.core.models import MORNING_DRAFT_COUNT, MorningTopic, ScheduledDraftRun

connection.ensure_connection()
User = get_user_model()
username = os.environ.get("APP_MORNING_USERNAME", "").strip()
owner = User.objects.filter(username=username, is_active=True).first() if username else None
if owner is None and not username:
    owner = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
topics = list(MorningTopic.objects.filter(owner=owner).order_by("topic").values_list("topic", flat=True)) if owner else []
owner_exists = owner is not None
today = timezone.localdate()
runs = list(ScheduledDraftRun.objects.filter(owner=owner, run_date=today).select_related("post").order_by("run_number")) if owner else []
print(f"Database: {connection.settings_dict['NAME']} @ {connection.settings_dict['HOST']}")
print(f"Selected user: {owner.username if owner else '(not found)'}")
print(f"Saved topic pool: {len(topics)} / 50")
for topic in topics:
    print(f"  - {topic}")
print(f"Today's draft slots: {len(runs)} / {MORNING_DRAFT_COUNT}")
for run in runs:
    topic = run.post.source_keyword if run.post_id else "(no topic assigned)"
    print(f"  - slot {run.run_number}: {run.status} · {topic}")

if not owner_exists:
    print("ERROR: Pass an active application username with -Username.")
    sys.exit(2)
if len(topics) < MORNING_DRAFT_COUNT:
    print("ERROR: Add at least two different topics on the Morning topics page first.")
    sys.exit(2)
if len(runs) >= MORNING_DRAFT_COUNT and all(
    (run.status == ScheduledDraftRun.Status.SUCCEEDED and run.post_id)
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
$commandArgs = @()
if ($Username) { $commandArgs += @("--username", $Username) }

$summary = @'
from django.utils import timezone
import os
from django.contrib.auth import get_user_model
from apps.core.models import ScheduledDraftRun

today = timezone.localdate()
username = os.environ.get("APP_MORNING_USERNAME", "").strip()
User = get_user_model()
owner = User.objects.filter(username=username, is_active=True).first() if username else User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
failed_count = 0
runs = ScheduledDraftRun.objects.filter(owner=owner, run_date=today).select_related("post").order_by("run_number") if owner else []
for run in runs:
    if run.status == ScheduledDraftRun.Status.FAILED:
        failed_count += 1
    if run.post_id:
        error = run.error_message or run.post.last_error
        print(f"slot {run.run_number}: {run.status} · {run.post.source_keyword} · draft #{run.post_id} · {run.post.status}")
    else:
        error = run.error_message
        print(f"slot {run.run_number}: {run.status} · no draft created")
    if error:
        print(f"  error: {error}")
print(f"FAILED_SLOTS={failed_count}")
'@

for ($attempt = 1; $attempt -le 3; $attempt++) {
    Write-Host ""
    Write-Host "Generation attempt $attempt of 3..." -ForegroundColor Cyan
    & $python manage.py generate_morning_drafts @commandArgs
    $generationExitCode = $LASTEXITCODE

    Write-Host "Today's draft results:" -ForegroundColor Cyan
    $summaryOutput = & $python manage.py shell -c $summary
    $summaryExitCode = $LASTEXITCODE
    $failedCount = -1
    foreach ($line in $summaryOutput) {
        if ($line -match '^FAILED_SLOTS=(\d+)$') {
            $failedCount = [int]$Matches[1]
        } else {
            Write-Host $line
        }
    }

    if ($summaryExitCode -ne 0 -or $failedCount -lt 0) {
        throw "Draft run ended, but the result summary could not be loaded. Check the database connection."
    }
    if ($failedCount -eq 0) {
        if ($generationExitCode -ne 0) {
            throw "The command returned an error even though no failed draft slots remain. Review the output above."
        }
        break
    }
    if ($attempt -eq 3) {
        throw "One or more drafts are still failed after 3 total attempts. Review the error above or open the failed draft in the app."
    }
    Write-Host "$failedCount slot(s) failed. Retrying only the failed slot(s) in a few seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds 3
}

Write-Host ""
Write-Host "Open https://linkedin-ai-content-agent-zpv4.onrender.com/ and review the new drafts." -ForegroundColor Green
