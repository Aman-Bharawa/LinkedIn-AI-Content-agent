"""Small, timeout-bounded clients for LinkedIn's official OAuth and Posts APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings

AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"  # noqa: S105  # nosec B105
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
REQUIRED_SCOPES = ("openid", "profile", "w_member_social")
PUBLISH_SCOPES = ("w_member_social",)


class LinkedInServiceError(Exception):
    """Safe integration error; never includes request headers or token values."""


class LinkedInAmbiguousError(LinkedInServiceError):
    """The publish request may have reached LinkedIn; automatic retry is unsafe."""


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    expires_in: int
    refresh_token: str | None
    refresh_token_expires_in: int | None
    scopes: tuple[str, ...]


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    if url not in {TOKEN_URL, USERINFO_URL}:
        raise LinkedInServiceError("LinkedIn endpoint is not allowed.")
    request = Request(url, data=data, headers=headers or {}, method=method)  # noqa: S310
    try:
        with urlopen(request, timeout=settings.LINKEDIN_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
            body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise LinkedInServiceError("LinkedIn returned an unexpectedly large response.")
            parsed = json.loads(body) if body else {}
            return response.status, dict(response.headers.items()), parsed
    except HTTPError as exc:
        # Do not surface the response body: providers sometimes echo request data.
        raise LinkedInServiceError(f"LinkedIn returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise LinkedInServiceError("LinkedIn could not be reached.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise LinkedInServiceError("LinkedIn returned an invalid response.") from None


def authorization_url(state: str) -> str:
    if not settings.LINKEDIN_CLIENT_ID or not settings.LINKEDIN_REDIRECT_URI:
        raise LinkedInServiceError("LinkedIn OAuth is not configured.")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.LINKEDIN_CLIENT_ID,
            "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
            "state": state,
            "scope": " ".join(REQUIRED_SCOPES),
        }
    )
    return f"{AUTHORIZATION_URL}?{query}"


def exchange_code(code: str) -> OAuthToken:
    if not settings.LINKEDIN_CLIENT_ID or not settings.LINKEDIN_CLIENT_SECRET:
        raise LinkedInServiceError("LinkedIn OAuth is not configured.")
    form = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.LINKEDIN_CLIENT_ID,
            "client_secret": settings.LINKEDIN_CLIENT_SECRET,
            "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
        }
    ).encode()
    _, _, payload = _request(
        TOKEN_URL,
        method="POST",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    access = payload.get("access_token")
    expires = payload.get("expires_in")
    if not isinstance(access, str) or not access or not isinstance(expires, int) or expires <= 0:
        raise LinkedInServiceError("LinkedIn returned an incomplete token response.")
    scope_value = payload.get("scope", "")
    scopes = tuple(unquote(scope_value).split()) if isinstance(scope_value, str) else ()
    return OAuthToken(
        access_token=access,
        expires_in=expires,
        refresh_token=payload.get("refresh_token") or None,
        refresh_token_expires_in=payload.get("refresh_token_expires_in"),
        scopes=scopes,
    )


def member_subject(access_token: str) -> str:
    _, _, payload = _request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 200:
        raise LinkedInServiceError("LinkedIn did not return a usable member identity.")
    return subject


def publish_text_post(*, access_token: str, author_urn: str, text: str) -> tuple[int, str]:
    if not settings.LINKEDIN_API_VERSION:
        raise LinkedInServiceError("LINKEDIN_API_VERSION is not configured.")
    body = json.dumps(
        {
            "author": author_urn,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
    ).encode()
    request = Request(
        POSTS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Linkedin-Version": settings.LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=settings.LINKEDIN_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
            response.read(1_000_001)
            urn = response.headers.get("x-restli-id", "")
            if response.status != 201 or not urn.startswith("urn:li:"):
                raise LinkedInAmbiguousError(
                    "LinkedIn accepted an unclear publication response; manual review is required."
                )
            return response.status, urn
    except HTTPError as exc:
        if 400 <= exc.code < 500:
            raise LinkedInServiceError(f"LinkedIn rejected the post (HTTP {exc.code}).") from None
        raise LinkedInAmbiguousError(
            f"LinkedIn returned HTTP {exc.code}; check LinkedIn before retrying."
        ) from None
    except (URLError, TimeoutError, OSError):
        raise LinkedInAmbiguousError(
            "The publish outcome is unknown; check LinkedIn before retrying."
        ) from None
