$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found at $python. Run 'uv sync' first."
}

Set-Location $projectRoot
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"

Write-Host "Refreshing approved-post queue and checking for due posts..." -ForegroundColor Cyan
$queueAndReport = @'
from django.db import connection
from django.utils import timezone
from apps.posts.models import LinkedInPost, PostStatus
from apps.posts.scheduling import refresh_publication_queue

connection.ensure_connection()
changed = refresh_publication_queue()
print(f"Database: {connection.settings_dict['NAME']} @ {connection.settings_dict['HOST']}")
print(f"Queue assignments changed: {changed}")
posts = LinkedInPost.objects.filter(status=PostStatus.APPROVED).order_by("scheduled_for", "approved_at", "pk")
now = timezone.now()
due_posts = posts.filter(
    scheduled_for__isnull=False,
    scheduled_for__lte=now,
    publication_queued_at__isnull=True,
    approved_by__is_active=True,
    approved_by__is_superuser=True,
)
print("Approved-post schedule:")
for post in posts:
    if post.scheduled_for:
        slot = timezone.localtime(post.scheduled_for).strftime("%Y-%m-%d %H:%M %Z")
    else:
        slot = "not scheduled"
    state = "already dispatched" if post.publication_queued_at else "waiting for its scheduled time"
    print(f"  - post #{post.pk}: {post.topic or '(untitled)'} | {slot} | {state}")
print(f"DUE_POST_COUNT={due_posts.count()}")
for post in due_posts.order_by("scheduled_for", "pk")[:1]:
    print(f"DUE_POST=post #{post.pk}: {post.topic or '(untitled)'}")
'@

$output = & $python manage.py shell -c $queueAndReport 2>&1
foreach ($line in $output) {
    if ($line -match '^DUE_POST_COUNT=') {
        continue
    }
    if ($line -match '^DUE_POST=') {
        Write-Host ($line -replace '^DUE_POST=', '') -ForegroundColor Yellow
        continue
    }
    Write-Host $line
}
if ($LASTEXITCODE -ne 0) {
    throw "Queue refresh failed. Check the output above."
}

$dueCount = 0
foreach ($line in $output) {
    if ($line -match '^DUE_POST_COUNT=(\d+)$') {
        $dueCount = [int]$Matches[1]
    }
}
if ($dueCount -eq 0) {
    Write-Host "No approved post is due yet. Run this script again at or after its displayed scheduled time." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "A due post will be sent to your connected LinkedIn account and become public." -ForegroundColor Red
Write-Host "Keep the Celery worker running. This script triggers the publisher task; the worker performs the LinkedIn request."
$answer = Read-Host "Type PUBLISH to continue"
if ($answer -cne "PUBLISH") {
    Write-Host "Cancelled. No publishing task was queued."
    exit 0
}

$dispatch = @'
from apps.linkedin.tasks import enqueue_due_publications

result = enqueue_due_publications.apply().get()
print(f"Publication dispatch result: {result}")
'@
Write-Host "Triggering the due-publication task..." -ForegroundColor Cyan
& $python manage.py shell -c $dispatch
if ($LASTEXITCODE -ne 0) {
    throw "Could not dispatch the due post. Check that the configured Redis broker is reachable."
}
