from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from . import health


@require_GET
@never_cache
def liveness(request: HttpRequest) -> JsonResponse:
    """The web process is up. Does not touch external services."""
    return JsonResponse({"status": "ok"})


@require_GET
@never_cache
def readiness(request: HttpRequest) -> JsonResponse:
    """The web process can reach PostgreSQL and Redis."""
    checks = {
        "database": "ok" if health.database_ok() else "unavailable",
        "redis": "ok" if health.redis_ok() else "unavailable",
    }
    ready = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ok" if ready else "unavailable", "checks": checks},
        status=200 if ready else 503,
    )


@require_GET
@login_required
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "core/home.html")
