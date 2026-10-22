from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_alter_scheduleddraftrun_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="MorningTopic",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("topic", models.CharField(max_length=255, unique=True)),
            ],
            options={"ordering": ["topic"]},
        ),
    ]
