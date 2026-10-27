from __future__ import annotations

import hashlib
import ipaddress

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from redis.exceptions import RedisError


class OwnerActionRateLimitMiddleware:
    """Bound authentication and costly actions using the shared cache."""

    LIMITS = {
        "/accounts/login/": (30, 300),
        "/django-admin/login/": (30, 300),
        "/accounts/sign-up/": (10, 3600),
        "/posts/generate/": (10, 60),
        "/suggest-topic/": (10, 60),
        "/linkedin/connect/": (10, 60),
    }
    PUBLISH_LIMIT = (5, 60)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        limit = self.LIMITS.get(request.path)
        if request.path.startswith("/linkedin/posts/") and request.path.endswith("/publish/"):
            limit = self.PUBLISH_LIMIT
        if request.path.startswith("/posts/") and request.path.endswith("/regenerate/"):
            limit = (10, 60)
        if (limit and request.method == "POST") or (
            request.path == "/linkedin/connect/" and request.method == "GET"
        ):
            address = self._client_ip(request)
            identity = request.POST.get("username", "").strip().casefold()
            limits = [(address, *(limit or self.PUBLISH_LIMIT))]
            if request.path in {"/accounts/login/", "/django-admin/login/"} and identity:
                limits.append((f"{address}:username:{identity}", 5, 300))
            for identity_key, maximum, window in limits:
                digest = hashlib.sha256(f"{identity_key}:{request.path}".encode()).hexdigest()
                key = f"owner-action:{digest}"
                try:
                    count = self._increment(key, window)
                except RedisError:
                    return HttpResponse(
                        "Rate limiting is temporarily unavailable. Please retry shortly.",
                        status=503,
                    )
                if count > maximum:
                    response = HttpResponse(
                        "Too many requests. Please try again shortly.", status=429
                    )
                    response["Retry-After"] = str(window)
                    return response
        return self.get_response(request)

    @staticmethod
    def _client_ip(request: HttpRequest) -> str:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded_for:
            forwarded_ip = forwarded_for.split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(forwarded_ip))
            except ValueError:
                pass
        return request.META.get("REMOTE_ADDR", "unknown")

    @staticmethod
    def _increment(key: str, window: int) -> int:
        if cache.add(key, 1, timeout=window):
            return 1
        try:
            return cache.incr(key)
        except ValueError:
            if cache.add(key, 1, timeout=window):
                return 1
            return cache.incr(key)


class SuperuserAdminMiddleware:
    """Keep the global Django admin limited to superusers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        is_admin_path = request.path == "/django-admin" or request.path.startswith(
            "/django-admin/"
        )
        if (
            is_admin_path
            and request.user.is_authenticated
            and not request.user.is_superuser
        ):
            return HttpResponseNotFound()
        return self.get_response(request)


class AdditionalSecurityHeadersMiddleware:
    """Set browser policies that Django does not add by default."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        if not settings.DEBUG:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
                "form-action 'self'; img-src 'self' data:; font-src 'self' data:; "
                "connect-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'",
            )
        return response
