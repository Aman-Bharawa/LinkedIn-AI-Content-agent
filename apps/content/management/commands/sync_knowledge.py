from typing import Any

from django.core.management.base import BaseCommand

from apps.content.knowledge import sync_knowledge


class Command(BaseCommand):
    help = "Import knowledge/*.md (facts, writing style, examples) into the database."

    def handle(self, *args: Any, **options: Any) -> None:
        report = sync_knowledge()
        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(warning))
        self.stdout.write(
            self.style.SUCCESS(
                f"Verified facts: {report.verified_facts}, unverified (ignored): "
                f"{report.unverified_facts}, subject areas: {report.subject_areas}, "
                f"approved examples: {report.approved_examples}, unapproved examples: "
                f"{report.unapproved_examples}, deactivated: {report.deactivated}."
            )
        )
