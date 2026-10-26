from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from apps.posts.tasks import run_morning_drafts


class Command(BaseCommand):
    help = "Generate one user's two morning drafts directly, without Celery or Redis."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            help="Application username to generate drafts for (defaults to the oldest superuser).",
        )

    def handle(self, *args: object, **options: object) -> None:
        User = get_user_model()
        username = options.get("username")
        if username:
            owner = User.objects.filter(username=username, is_active=True).first()
        else:
            owner = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
        if owner is None:
            raise CommandError("No matching active user. Create an account or pass --username.")
        result = run_morning_drafts(synchronous=True, owner=owner)
        self.stdout.write(result)
        if "failed_0_" not in result:
            raise CommandError(
                "One or more morning drafts failed. Check the command output and draft history."
            )
