
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('posts', '0004_publicationschedulesettings'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='linkedinpost',
            name='owner',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='linkedin_posts', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='publicationschedulesettings',
            name='owner',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='publication_schedule_settings', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='publicationschedulesettings',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
    ]
