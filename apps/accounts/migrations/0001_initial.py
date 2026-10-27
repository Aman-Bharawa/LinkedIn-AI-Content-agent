
import apps.core.security
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='LinkedInAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('member_urn', models.CharField(help_text='LinkedIn member URN, e.g. urn:li:person:abc123.', max_length=255, unique=True)),
                ('access_token', apps.core.security.EncryptedTextField()),
                ('refresh_token', apps.core.security.EncryptedTextField(blank=True, null=True)),
                ('access_token_expires_at', models.DateTimeField()),
                ('refresh_token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('scopes', models.JSONField(blank=True, default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('connected_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('disconnected_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'LinkedIn account',
                'constraints': [models.UniqueConstraint(condition=models.Q(('is_active', True)), fields=('is_active',), name='linkedin_account_single_active'), models.CheckConstraint(condition=models.Q(('member_urn__startswith', 'urn:li:')), name='linkedin_account_member_urn_format')],
            },
        ),
    ]
