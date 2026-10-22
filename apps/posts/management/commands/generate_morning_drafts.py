from django.core.management.base import BaseCommand, CommandError

from apps.posts.tasks import run_morning_drafts


class Command(BaseCommand):
    help = "Generate today's two morning drafts directly, without Celery or Redis."

    def handle(self, *args: object, **options: object) -> None:
        result = run_morning_drafts(synchronous=True)
        self.stdout.write(result)
        if "failed_0_" not in result:
            raise CommandError(
                "One or more morning drafts failed. Check the command output and draft history."
            )
