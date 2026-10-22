from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import LinkedInAccount, is_owner
from apps.posts.forms import GeneratePostForm
from apps.posts.models import (
    LinkedInPost,
    PostReview,
    PostStatus,
    PublicationScheduleSettings,
)

from . import health
from .forms import MorningTopicForm
from .models import MORNING_DRAFT_COUNT, MorningTopic, ScheduledDraftRun


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


@require_POST
@login_required
def suggest_topic(request: HttpRequest) -> JsonResponse:
    if not is_owner(request.user):
        return JsonResponse({"error": "Owner access required."}, status=403)
    from apps.ai.services import DraftGenerationError, suggest_topic_idea

    try:
        return JsonResponse(
            {"topic": suggest_topic_idea(exclude_topic=request.POST.get("current_topic", ""))}
        )
    except DraftGenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=503)


@login_required
@require_http_methods(["GET", "POST"])
def morning_topics(request: HttpRequest) -> HttpResponse:
    if not is_owner(request.user):
        return HttpResponse("Owner access required.", status=403)

    if request.method == "POST" and "delete_topic" in request.POST:
        try:
            topic_id = int(request.POST.get("delete_topic", ""))
        except (TypeError, ValueError):
            topic_id = None
        deleted, _ = MorningTopic.objects.filter(pk=topic_id).delete() if topic_id else (0, {})
        if deleted:
            messages.success(request, "Removed the topic from your morning list.")
        else:
            messages.error(request, "That topic was already removed or does not exist.")
        return redirect("core:morning-topics")

    if request.method == "POST":
        form = MorningTopicForm(request.POST)
        if form.is_valid():
            topic = form.cleaned_data["topic"]
            if MorningTopic.objects.filter(topic__iexact=topic).exists():
                form.add_error("topic", "That topic is already in your list.")
            elif MorningTopic.objects.count() >= 50:
                form.add_error("topic", "You can save up to 50 topics.")
            else:
                MorningTopic.objects.create(topic=topic)
                messages.success(request, f"Added “{topic}” to your morning topic list.")
                return redirect("core:morning-topics")
    else:
        form = MorningTopicForm()

    return render(
        request,
        "core/morning_topics.html",
        {
            "form": form,
            "topics": MorningTopic.objects.all(),
            "topic_count": MorningTopic.objects.count(),
        },
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
            "draft_count": LinkedInPost.objects.count(),
            "review_count": LinkedInPost.objects.filter(
                status__in=[PostStatus.PENDING_APPROVAL, PostStatus.NEEDS_MANUAL_REVIEW]
            ).count(),
            "published_count": LinkedInPost.objects.filter(status=PostStatus.PUBLISHED).count(),
            "status_choices": [("all", "All statuses"), *PostStatus.choices],
            "selected_status": selected_status,
            "search_term": search_term,
            "display_timezone": timezone.get_current_timezone_name(),
            "publication_settings": PublicationScheduleSettings.get_solo(),
            "linkedin_account": LinkedInAccount.objects.filter(is_active=True).first(),
            "scheduled_runs": ScheduledDraftRun.objects.filter(
                run_date__gte=timezone.localdate() - timedelta(days=1)
            ).select_related("post")[:MORNING_DRAFT_COUNT],
        },
    )
