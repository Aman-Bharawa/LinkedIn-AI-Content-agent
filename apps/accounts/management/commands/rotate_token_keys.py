"""Re-encrypt every stored LinkedIn token with the newest TOKEN_ENCRYPTION_KEYS entry.

Rotation procedure (also in README.md):
1. Generate a new key and put it FIRST in TOKEN_ENCRYPTION_KEYS, keeping the old key after it.
2. Restart, then run: manage.py rotate_token_keys
3. Remove the old key from TOKEN_ENCRYPTION_KEYS and restart again.
"""

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import LinkedInAccount


class Command(BaseCommand):
    help = "Re-encrypt stored LinkedIn tokens with the primary TOKEN_ENCRYPTION_KEYS key."

    def handle(self, *args: Any, **options: Any) -> None:
        count = 0
        with transaction.atomic():
            for account in LinkedInAccount.objects.select_for_update().order_by("pk"):
                # Loading decrypts with any configured key; saving encrypts with the first.
                account.save(update_fields=["access_token", "refresh_token", "updated_at"])
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Re-encrypted tokens for {count} account(s)."))
