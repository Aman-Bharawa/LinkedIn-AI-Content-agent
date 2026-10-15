from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


GROUPS = [
    ("Founder/business", "founder-business", Decimal("30.00")),
    ("AI/technology", "ai-technology", Decimal("25.00")),
    ("Startup/product", "startup-product", Decimal("20.00")),
    ("Engineering/team", "engineering-team", Decimal("15.00")),
    ("Company", "company", Decimal("10.00")),
]

PILLARS = [
    ("Founder lessons", "founder-lessons", "founder-business"),
    ("Startup product development", "startup-product-development", "startup-product"),
    ("AI and software development", "ai-and-software-development", "ai-technology"),
    ("Engineering leadership", "engineering-leadership", "engineering-team"),
    ("Developer productivity", "developer-productivity", "engineering-team"),
    ("Hiring and team building", "hiring-and-team-building", "engineering-team"),
    ("Client lessons", "client-lessons", "founder-business"),
    ("Technology business", "technology-business", "founder-business"),
    ("Startup mistakes", "startup-mistakes", "startup-product"),
    ("Product strategy", "product-strategy", "startup-product"),
    ("MENA startup ecosystem", "mena-startup-ecosystem", "founder-business"),
    ("India engineering ecosystem", "india-engineering-ecosystem", "engineering-team"),
    ("Company lessons", "company-lessons", "company"),
]


def seed_strategy(apps, schema_editor):
    ContentGroup = apps.get_model("content", "ContentGroup")
    ContentPillar = apps.get_model("content", "ContentPillar")
    db = schema_editor.connection.alias

    groups = {}
    for name, slug, share in GROUPS:
        group, _ = ContentGroup.objects.using(db).update_or_create(
            slug=slug,
            defaults={"name": name, "target_share": share, "is_active": True},
        )
        groups[slug] = group

    for name, slug, group_slug in PILLARS:
        ContentPillar.objects.using(db).update_or_create(
            slug=slug,
            defaults={"name": name, "group_id": groups[group_slug].pk, "is_active": True},
        )


class Migration(migrations.Migration):
    dependencies = [("content", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="ContentGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                ("target_share", models.DecimalField(decimal_places=2, help_text="Target percentage of posts allocated to this group; active groups must total 100%.", max_digits=5)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"],
                "constraints": [models.CheckConstraint(condition=models.Q(target_share__gte=0, target_share__lte=100), name="content_group_share_range")],
            },
        ),
        migrations.AddField(
            model_name="contentpillar",
            name="group",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="pillars", to="content.contentgroup"),
        ),
        migrations.RunPython(seed_strategy, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="contentpillar",
            name="group",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="pillars", to="content.contentgroup"),
        ),
    ]
