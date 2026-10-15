from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("posts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="linkedinpost",
            name="origin",
            field=models.CharField(
                choices=[("keyword", "Keyword"), ("auto", "Auto draft")],
                default="keyword",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="linkedinpost",
            name="source_keyword",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddIndex(
            model_name="linkedinpost",
            index=models.Index(fields=["origin", "-created_at"], name="post_origin_created_idx"),
        ),
    ]
