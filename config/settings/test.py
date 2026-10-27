from cryptography.fernet import Fernet

from . import base
from .base import *  # noqa: F403

TOKEN_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

if not base.env.bool("CI", default=False):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
