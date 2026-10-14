"""Dependency checks used by the readiness endpoint.

Each check returns True/False and never raises, so callers can report status
without leaking connection strings or exception details.
"""

import logging

import redis
from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)


def database_ok() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.warning("Readiness check failed: database unavailable", exc_info=False)
        return False
    return True


def redis_ok() -> bool:
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        logger.warning("Readiness check failed: redis unavailable", exc_info=False)
        return False
