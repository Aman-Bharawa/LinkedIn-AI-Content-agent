from __future__ import annotations

import hashlib

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse


class OwnerActionRateLimitMiddleware:
    """Bound costly owner actions by remote IP; the shared Redis cache coordinates workers."""

    LIMITS = {
        "/posts/generate/": (10, 60),
        "/linkedin/connect/": (10, 60),
    }
    PUBLISH_LIMIT = (5, 60)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        limit = self.LIMITS.get(request.path)
        if request.path.startswith("/linkedin/posts/") and request.path.endswith("/publish/"):
            limit = self.PUBLISH_LIMIT
        if (limit and request.method == "POST") or (
            request.path == "/linkedin/connect/" and request.method == "GET"
        ):
            maximum, window = limit or self.PUBLISH_LIMIT
            address = request.META.get("REMOTE_ADDR", "unknown")
            digest = hashlib.sha256(f"{address}:{request.path}".encode()).hexdigest()
            key = f"owner-action:{digest}"
            if not cache.add(key, 1, timeout=window):
                count = cache.incr(key)
                if count > maximum:
                    return HttpResponse("Too many requests. Please try again shortly.", status=429)
        return self.get_response(request)
