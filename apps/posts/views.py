from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import is_owner
from apps.ai.services import DraftGenerationError, generate_linkedin_draft
from apps.content.models import ContentPillar
from apps.core.models import AuditAction, AuditEvent

from .forms import GeneratePostForm, PostEditForm
from .models import LinkedInPost, PostOrigin, PostStatus, PostVersion, VersionKind
from .state import InvalidTransition, transition_post

EDITABLE_STATUSES = (PostStatus.PENDING_APPROVAL, PostStatus.NEEDS_MANUAL_REVIEW)
QUEUE_STATUSES = (PostStatus.PENDING_APPROVAL, PostStatus.NEEDS_MANUAL_REVIEW)


def owner_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    @login_required
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not is_owner(request.user):
            return HttpResponseForbidden("Owner access required.")
        return view(request, *args, **kwargs)

    return wrapped


@owner_required
@require_POST
def generate_post(request: HttpRequest) -> HttpResponse:
    form = GeneratePostForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a keyword with at least two characters.")
        return redirect("core:home")

    keyword = form.cleaned_data["keyword"].strip()
    post = LinkedInPost.objects.create(
        topic=keyword,
        source_keyword=keyword,
        origin=PostOrigin.KEYWORD,
        status=PostStatus.DRAFT,
    )
    post = transition_post(post, PostStatus.GENERATING, actor=request.user)

    try:
        generated = generate_linkedin_draft(keyword)
    except DraftGenerationError as exc:
        transition_post(post, PostStatus.FAILED, actor=request.user, reason=str(exc))
        messages.error(request, str(exc))
        return redirect("core:home")

    with transaction.atomic():
        post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
        post.topic = generated.topic
        post.pillar = ContentPillar.objects.get(slug=generated.pillar_slug)
        post.content_topic_id = generated.content_topic_id
        post.hook = generated.hook
        post.content = generated.content
        post.hashtags = generated.hashtags
        post.original_ai_content = generated.content
        post.ai_model = generated.model
        post.prompt_version = generated.prompt_version
        post.generation_attempts = 1
        post.save()
        PostVersion.objects.create(
            post=post,
            version_number=1,
            kind=VersionKind.AI_GENERATED,
            topic=post.topic,
            hook=post.hook,
            content=post.content,
            hashtags=post.hashtags,
            ai_model=post.ai_model,
            prompt_version=post.prompt_version,
        )
        post = transition_post(post, PostStatus.NEEDS_MANUAL_REVIEW, actor=request.user)

    messages.success(request, "Draft generated. Review every claim before approving it.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_http_methods(["GET", "POST"])
def edit_post(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(LinkedInPost, pk=post_id)
    if post.status not in EDITABLE_STATUSES:
        messages.error(request, "This post can no longer be edited in its current state.")
        return redirect("posts:detail", post_id=post.pk)

    initial = {
        "topic": post.topic,
        "hook": post.hook,
        "content": post.content,
        "hashtags": " ".join(f"#{tag}" for tag in post.hashtags),
    }
    form = PostEditForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
            if post.status not in EDITABLE_STATUSES:
                messages.error(request, "This post changed state and can no longer be edited.")
                return redirect("posts:detail", post_id=post.pk)
            post.topic = form.cleaned_data["topic"].strip()
            post.hook = form.cleaned_data["hook"].strip()
            post.content = form.cleaned_data["content"].strip()
            post.hashtags = form.cleaned_data["hashtags"]
            post.is_manually_edited = True
            post.save()
            PostVersion.objects.create(
                post=post,
                kind=VersionKind.OWNER_EDIT,
                topic=post.topic,
                hook=post.hook,
                content=post.content,
                hashtags=post.hashtags,
                created_by=cast(Any, request.user),
            )
            AuditEvent.objects.create(
                actor=cast(Any, request.user),
                action=AuditAction.EDIT,
                post=post,
                from_status=post.status,
                to_status=post.status,
            )
        messages.success(request, "Your edits are saved. The original AI draft remains in history.")
        return redirect("posts:detail", post_id=post.pk)

    return render(request, "posts/edit.html", {"post": post, "form": form})


@owner_required
@require_GET
def post_detail(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(
        LinkedInPost.objects.select_related("pillar", "approved_by"), pk=post_id
    )
    return render(
        request,
        "posts/detail.html",
        {
            "post": post,
            "versions": post.versions.all(),
            "can_edit": post.status in EDITABLE_STATUSES,
            "can_review": post.status in QUEUE_STATUSES,
        },
    )


@owner_required
@require_POST
def approve_post(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(LinkedInPost, pk=post_id)
    try:
        post = transition_post(post, PostStatus.APPROVED, actor=request.user)
    except InvalidTransition:
        messages.error(request, "This post is no longer waiting for owner approval.")
        return redirect("posts:detail", post_id=post.pk)
    messages.success(request, "Post approved. Publishing remains a separate manual step.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_POST
def reject_post(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(LinkedInPost, pk=post_id)
    reason = request.POST.get("reason", "").strip()[:2000]
    try:
        post = transition_post(post, PostStatus.REJECTED, actor=request.user, reason=reason)
    except InvalidTransition:
        messages.error(request, "This post is no longer waiting for owner review.")
        return redirect("posts:detail", post_id=post.pk)
    messages.info(
        request, "Post rejected and removed from the approval queue; it remains in history."
    )
    return redirect("core:home")
