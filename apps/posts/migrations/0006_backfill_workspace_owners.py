from django.conf import settings
from django.db import migrations


def assign_existing_data_to_legacy_owner(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    owner = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
    if owner is None:
        owner = User.objects.filter(is_active=True).order_by("pk").first()
    if owner is None:
        return

    LinkedInAccount = apps.get_model("accounts", "LinkedInAccount")
    LinkedInPost = apps.get_model("posts", "LinkedInPost")
    PublicationScheduleSettings = apps.get_model("posts", "PublicationScheduleSettings")
    ContentKnowledge = apps.get_model("content", "ContentKnowledge")
    ContentTopic = apps.get_model("content", "ContentTopic")
    MorningTopic = apps.get_model("core", "MorningTopic")
    ScheduledDraftRun = apps.get_model("core", "ScheduledDraftRun")

    LinkedInAccount.objects.filter(owner__isnull=True).update(owner_id=owner.pk)
    LinkedInPost.objects.filter(owner__isnull=True).update(owner_id=owner.pk)
    PublicationScheduleSettings.objects.filter(owner__isnull=True).update(owner_id=owner.pk)
    ContentKnowledge.objects.filter(
        owner__isnull=True, source__startswith="owner-dashboard:"
    ).update(owner_id=owner.pk)
    ContentTopic.objects.filter(owner__isnull=True).update(owner_id=owner.pk)
    MorningTopic.objects.filter(owner__isnull=True).update(owner_id=owner.pk)
    for run in ScheduledDraftRun.objects.filter(owner__isnull=True).iterator():
        post_owner_id = (
            LinkedInPost.objects.filter(pk=run.post_id).values_list("owner_id", flat=True).first()
            if run.post_id
            else None
        )
        run.owner_id = post_owner_id or owner.pk
        run.save(update_fields=["owner"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_remove_linkedinaccount_linkedin_account_single_active_and_more"),
        ("content", "0004_contentknowledge_owner_contenttopic_owner_and_more"),
        ("core", "0005_remove_scheduleddraftrun_scheduled_run_date_number_uniq_and_more"),
        ("posts", "0005_linkedinpost_owner_publicationschedulesettings_owner_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(assign_existing_data_to_legacy_owner, migrations.RunPython.noop)]
