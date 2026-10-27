
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_morningtopic'),
        ('posts', '0005_linkedinpost_owner_publicationschedulesettings_owner_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='scheduleddraftrun',
            name='scheduled_run_date_number_uniq',
        ),
        migrations.AddField(
            model_name='morningtopic',
            name='owner',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='morning_topics', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='scheduleddraftrun',
            name='owner',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='scheduled_draft_runs', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='morningtopic',
            name='topic',
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name='morningtopic',
            constraint=models.UniqueConstraint(condition=models.Q(('owner__isnull', True)), fields=('topic',), name='morning_topic_global_uniq'),
        ),
        migrations.AddConstraint(
            model_name='morningtopic',
            constraint=models.UniqueConstraint(condition=models.Q(('owner__isnull', False)), fields=('owner', 'topic'), name='morning_topic_owner_uniq'),
        ),
        migrations.AddConstraint(
            model_name='scheduleddraftrun',
            constraint=models.UniqueConstraint(condition=models.Q(('owner__isnull', False)), fields=('owner', 'run_date', 'run_number'), name='scheduled_run_owner_date_number_uniq'),
        ),
        migrations.AddConstraint(
            model_name='scheduleddraftrun',
            constraint=models.UniqueConstraint(condition=models.Q(('owner__isnull', True)), fields=('run_date', 'run_number'), name='scheduled_run_global_date_number_uniq'),
        ),
    ]
