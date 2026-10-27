
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_linkedinaccount_display_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='linkedinaccount',
            name='linkedin_account_single_active',
        ),
        migrations.AddField(
            model_name='linkedinaccount',
            name='owner',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='linkedin_accounts', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='linkedinaccount',
            name='member_urn',
            field=models.CharField(help_text='LinkedIn member URN, e.g. urn:li:person:abc123.', max_length=255),
        ),
        migrations.AddConstraint(
            model_name='linkedinaccount',
            constraint=models.UniqueConstraint(condition=models.Q(('is_active', True)), fields=('owner',), name='linkedin_account_owner_active_uniq'),
        ),
        migrations.AddConstraint(
            model_name='linkedinaccount',
            constraint=models.UniqueConstraint(fields=('owner', 'member_urn'), name='linkedin_account_owner_member_uniq'),
        ),
    ]
