import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import urlparse

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from apps.accounts.models import is_owner
from apps.core.models import AuditAction, AuditEvent
from apps.posts.forms import GeneratePostForm
from apps.posts.models import LinkedInPost, PostOrigin, PostStatus
from apps.posts.scheduling import refresh_publication_queue
from apps.posts.state import InvalidTransition, transition_post

from .models import ExtensionAccessToken, ExtensionPairingCode

PAIRING_LIFETIME = timedelta(minutes=5)
TOKEN_LIFETIME = timedelta(days=90)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_body(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _extension_origin(request: HttpRequest) -> str | None:
    origin = request.headers.get("Origin", "")
    parsed = urlparse(origin)
    if parsed.scheme == "chrome-extension" and parsed.netloc and not parsed.path:
        return origin
    return None


def _cors(response: JsonResponse, origin: str | None) -> JsonResponse:
    if origin:
        response["Access-Control-Allow-Origin"] = origin
        response["Vary"] = "Origin"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response["Access-Control-Max-Age"] = "600"
    response["Cache-Control"] = "no-store"
    return response


def _options(request: HttpRequest) -> JsonResponse:
    return _cors(JsonResponse({}, status=204), _extension_origin(request))


def _token_owner(request: HttpRequest):
    origin = _extension_origin(request)
    auth = request.headers.get("Authorization", "")
    scheme, _, raw_token = auth.partition(" ")
    if not origin or scheme.lower() != "bearer" or not raw_token:
        return None, origin
    token = ExtensionAccessToken.objects.select_related("owner").filter(
        token_digest=_digest(raw_token), revoked_at__isnull=True,
        expires_at__gt=timezone.now(), owner__is_active=True,
    ).first()
    return (token.owner if token else None), origin


@login_required
@require_GET
def pair_page(request: HttpRequest):
    if not is_owner(request.user):
        return JsonResponse({"error": "Account unavailable."}, status=403)
    raw_code = secrets.token_urlsafe(24)
    ExtensionPairingCode.objects.create(
        owner=request.user,
        code_digest=_digest(raw_code),
        expires_at=timezone.now() + PAIRING_LIFETIME,
    )
    response = render(
        request,
        "extensions/pair.html",
        {"pairing_code": raw_code, "expires_minutes": int(PAIRING_LIFETIME.total_seconds() / 60)},
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def exchange_pairing_code(request: HttpRequest):
    origin = _extension_origin(request)
    if not origin:
        return _cors(JsonResponse({"error": "Extension origin required."}, status=403), origin)
    if request.method == "OPTIONS":
        return _options(request)
    if request.method != "POST":
        return _cors(JsonResponse({"error": "Method not allowed."}, status=405), origin)
    data = _json_body(request)
    code = data.get("code", "")
    if not isinstance(code, str) or len(code) > 100:
        return _cors(JsonResponse({"error": "Invalid pairing code."}, status=400), origin)
    with transaction.atomic():
        pairing = ExtensionPairingCode.objects.select_for_update().select_related("owner").filter(
            code_digest=_digest(code), used_at__isnull=True, expires_at__gt=timezone.now(),
            owner__is_active=True,
        ).first()
        if pairing is None:
            return _cors(JsonResponse({"error": "Pairing code is invalid or expired."}, status=400), origin)
        pairing.used_at = timezone.now()
        pairing.save(update_fields=["used_at"])
        raw_token = secrets.token_urlsafe(40)
        ExtensionAccessToken.objects.create(
            owner=pairing.owner,
            token_digest=_digest(raw_token),
            expires_at=timezone.now() + TOKEN_LIFETIME,
        )
    return _cors(JsonResponse({"token": raw_token, "expires_in": int(TOKEN_LIFETIME.total_seconds())}), origin)


@csrf_exempt
def drafts_api(request: HttpRequest):
    origin = _extension_origin(request)
    if not origin:
        return _cors(JsonResponse({"error": "Extension origin required."}, status=403), origin)
    if request.method == "OPTIONS":
        return _options(request)
    owner, origin = _token_owner(request)
    if owner is None:
        return _cors(JsonResponse({"error": "Extension connection expired. Reconnect to continue."}, status=401), origin)
    if request.method == "GET":
        posts = LinkedInPost.objects.filter(owner=owner).order_by("-created_at")[:20]
        results = [
            {
                "id": post.pk,
                "topic": post.topic,
                "hook": post.hook,
                "content": post.content,
                "hashtags": post.hashtags,
                "status": post.status,
                "error": post.last_error,
                "scheduled_for": post.scheduled_for.isoformat() if post.scheduled_for else None,
                "url": request.build_absolute_uri(reverse("posts:detail", args=[post.pk])),
            }
            for post in posts
        ]
        return _cors(JsonResponse({"drafts": results}), origin)
    if request.method != "POST":
        return _cors(JsonResponse({"error": "Method not allowed."}, status=405), origin)
    data = _json_body(request)
    keyword_value = data.get("keyword", "")
    if not isinstance(keyword_value, str):
        keyword_value = ""
    form = GeneratePostForm({"keyword": keyword_value})
    if not form.is_valid():
        return _cors(JsonResponse({"error": "Enter a topic between 2 and 255 characters."}, status=400), origin)
    keyword = form.cleaned_data["keyword"].strip()
    post = LinkedInPost.objects.create(
        owner=owner,
        topic=keyword,
        source_keyword=keyword,
        origin=PostOrigin.KEYWORD,
        status=PostStatus.DRAFT,
    )
    post = transition_post(post, PostStatus.GENERATING, actor=owner)
    try:
        from apps.posts.tasks import generate_keyword_post

        generate_keyword_post.delay(post.pk, owner.pk)
    except Exception:
        transition_post(
            post,
            PostStatus.FAILED,
            actor=owner,
            reason="The background worker could not accept this generation job.",
        )
        return _cors(JsonResponse({"error": "Draft generation could not be queued. Check that the worker is running."}, status=503), origin)
    return _cors(JsonResponse({"id": post.pk, "status": post.status}, status=202), origin)


@csrf_exempt
def approve_api(request: HttpRequest, post_id: int):
    origin = _extension_origin(request)
    if not origin:
        return _cors(JsonResponse({"error": "Extension origin required."}, status=403), origin)
    if request.method == "OPTIONS":
        return _options(request)
    if request.method != "POST":
        return _cors(JsonResponse({"error": "Method not allowed."}, status=405), origin)
    owner, origin = _token_owner(request)
    if owner is None:
        return _cors(JsonResponse({"error": "Extension connection expired. Reconnect to continue."}, status=401), origin)
    post = LinkedInPost.objects.filter(owner=owner).filter(pk=post_id).first()
    if post is None:
        return _cors(JsonResponse({"error": "Draft not found."}, status=404), origin)
    if post.status not in (PostStatus.PENDING_APPROVAL, PostStatus.NEEDS_MANUAL_REVIEW):
        return _cors(JsonResponse({"error": "This draft is not waiting for approval."}, status=409), origin)
    try:
        with transaction.atomic():
            post = transition_post(post, PostStatus.APPROVED, actor=owner)
            post.publication_queued_at = None
            post.save(update_fields=["publication_queued_at", "updated_at"])
            refresh_publication_queue(owner=owner)
            post.refresh_from_db(fields=["scheduled_for"])
            AuditEvent.objects.create(
                actor=owner,
                action=AuditAction.STATUS_CHANGE,
                post=post,
                from_status=PostStatus.APPROVED,
                to_status=PostStatus.APPROVED,
                metadata={"scheduled_for": post.scheduled_for.isoformat()} if post.scheduled_for else {},
            )
    except InvalidTransition:
        return _cors(JsonResponse({"error": "This draft is no longer waiting for approval."}, status=409), origin)
    return _cors(JsonResponse({
        "message": "Draft approved and added to the publishing queue.",
        "scheduled_for": post.scheduled_for.isoformat() if post.scheduled_for else None,
        "status": post.status,
    }), origin)


@csrf_exempt
def revoke_api(request: HttpRequest):
    origin = _extension_origin(request)
    if not origin:
        return _cors(JsonResponse({"error": "Extension origin required."}, status=403), origin)
    if request.method == "OPTIONS":
        return _options(request)
    if request.method != "POST":
        return _cors(JsonResponse({"error": "Method not allowed."}, status=405), origin)
    owner, origin = _token_owner(request)
    if owner is None:
        return _cors(JsonResponse({"error": "Extension connection expired."}, status=401), origin)
    scheme, _, raw_token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and raw_token:
        ExtensionAccessToken.objects.filter(token_digest=_digest(raw_token), owner=owner).update(
            revoked_at=timezone.now()
        )
    return _cors(JsonResponse({"message": "Extension disconnected."}), origin)
