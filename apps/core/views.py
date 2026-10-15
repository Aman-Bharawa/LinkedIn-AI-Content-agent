from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.accounts.models import LinkedInAccount, is_owner
from apps.posts.forms import GeneratePostForm
from apps.posts.models import LinkedInPost, PostReview, PostStatus

from . import health
from .models import ScheduledDraftRun


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
    if not is_owner(request.user):
        return HttpResponse("Owner access required.", status=403)
    selected_status = request.GET.get("status", "all")
    search_term = request.GET.get("q", "").strip()[:100]
    posts = LinkedInPost.objects.select_related("pillar").prefetch_related(
        Prefetch("reviews", queryset=PostReview.objects.order_by("-reviewed_at"))
    )
    if selected_status in PostStatus.values:
        posts = posts.filter(status=selected_status)
    elif selected_status != "all":
        selected_status = "all"
    if search_term:
        posts = posts.filter(topic__icontains=search_term)
    paginator = Paginator(posts.order_by("-created_at"), 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "core/home.html",
        {
            "form": GeneratePostForm(),
            "page_obj": page_obj,
            "status_choices": [("all", "All statuses"), *PostStatus.choices],
            "selected_status": selected_status,
            "search_term": search_term,
            "linkedin_account": LinkedInAccount.objects.filter(is_active=True).first(),
            "latest_scheduled_run": ScheduledDraftRun.objects.filter(
                run_date__gte=timezone.localdate() - timedelta(days=1)
            )
            .select_related("post")
            .first(),
        },
    )
