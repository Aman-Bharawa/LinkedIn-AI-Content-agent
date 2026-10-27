
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("posts", "0002_post_origin"),
    ]

    operations = [
        migrations.AddField(
            model_name="linkedinpost",
            name="publication_queued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
