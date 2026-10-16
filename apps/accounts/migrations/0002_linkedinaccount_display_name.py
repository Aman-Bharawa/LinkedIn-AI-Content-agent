from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="linkedinaccount",
            name="display_name",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
