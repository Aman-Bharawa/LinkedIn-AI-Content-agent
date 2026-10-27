
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0002_content_strategy'),
    ]

    operations = [
        migrations.AddField(
            model_name='contenttopic',
            name='post_format',
            field=models.CharField(choices=[('lesson', 'Lesson'), ('how_to', 'How to'), ('contrarian', 'Contrarian'), ('framework', 'Framework'), ('myth_vs_reality', 'Myth vs reality'), ('question', 'Question')], default='lesson', max_length=32),
        ),
    ]
