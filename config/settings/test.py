from cryptography.fernet import Fernet

from .base import *  # noqa: F403

# Throwaway key generated per test run; real keys never reach the test suite.
TOKEN_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

# Fast hashing for tests only.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Tests never talk to a real broker; tasks run inline when invoked.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
