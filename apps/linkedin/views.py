from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import LinkedInAccount, is_owner
from apps.core.models import AuditAction, AuditEvent

from .services import (
    REQUIRED_SCOPES,
    LinkedInServiceError,
    authorization_url,
    exchange_code,
    member_profile,
)


def _owner_denied(request: HttpRequest) -> HttpResponse | None:
    if not is_owner(request.user):
        return HttpResponseForbidden("Owner access required.")
    return None


@require_GET
@login_required
def connect(request: HttpRequest) -> HttpResponse:
    denied = _owner_denied(request)
    if denied:
        return denied
    state = secrets.token_urlsafe(32)
    request.session["linkedin_oauth_state"] = state
    request.session.set_expiry(600)
    try:
        return redirect(authorization_url(state))
    except LinkedInServiceError as exc:
        messages.error(request, str(exc))
        return redirect("core:posts-dashboard")


@require_GET
@login_required
def callback(request: HttpRequest) -> HttpResponse:
    denied = _owner_denied(request)
    if denied:
        return denied
    expected_state = request.session.pop("linkedin_oauth_state", None)
    returned_state = request.GET.get("state")
    code = request.GET.get("code", "")
    if not expected_state or not secrets.compare_digest(expected_state, returned_state or ""):
        messages.error(request, "LinkedIn connection could not be verified. Start again.")
        return redirect("core:posts-dashboard")
    if request.GET.get("error") or not code:
        messages.error(request, "LinkedIn authorization was declined or did not complete.")
        return redirect("core:posts-dashboard")
    if not settings.LINKEDIN_REDIRECT_URI.startswith("https://") and not settings.DEBUG:
        messages.error(request, "LinkedIn callback must use HTTPS outside local development.")
        return redirect("core:posts-dashboard")
    try:
        token = exchange_code(code)
        if not set(REQUIRED_SCOPES).issubset(token.scopes):
            raise LinkedInServiceError("LinkedIn did not grant all required permissions.")
        subject, display_name = member_profile(token.access_token)
    except LinkedInServiceError as exc:
        messages.error(request, str(exc))
        return redirect("core:posts-dashboard")

    now = timezone.now()
    member_urn = f"urn:li:person:{subject}"
    with transaction.atomic():
        LinkedInAccount.objects.select_for_update().filter(is_active=True).update(
            is_active=False, disconnected_at=now
        )
        account, _ = LinkedInAccount.objects.update_or_create(
            member_urn=member_urn,
            defaults={
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "display_name": display_name,
                "access_token_expires_at": now + timedelta(seconds=token.expires_in),
                "refresh_token_expires_at": (
                    now + timedelta(seconds=token.refresh_token_expires_in)
                    if token.refresh_token_expires_in
                    else None
                ),
                "scopes": list(token.scopes),
                "is_active": True,
                "connected_at": now,
                "disconnected_at": None,
            },
        )
        AuditEvent.objects.create(
            actor=cast(Any, request.user),
            action=AuditAction.CONNECT,
            metadata={"account_id": account.pk},
        )
    messages.success(request, "LinkedIn account connected securely.")
    return redirect("core:posts-dashboard")


@require_POST
@login_required
def disconnect(request: HttpRequest) -> HttpResponse:
    denied = _owner_denied(request)
    if denied:
        return denied
    account = LinkedInAccount.objects.filter(is_active=True).first()
    if account:
        account.is_active = False
        account.disconnected_at = timezone.now()
        account.save(update_fields=["is_active", "disconnected_at", "updated_at"])
        AuditEvent.objects.create(
            actor=cast(Any, request.user),
            action=AuditAction.DISCONNECT,
            metadata={"account_id": account.pk},
        )
        messages.success(request, "LinkedIn was disconnected. Revoke this app from LinkedIn too.")
    else:
        messages.info(request, "No LinkedIn account is connected.")
    return redirect("core:posts-dashboard")
