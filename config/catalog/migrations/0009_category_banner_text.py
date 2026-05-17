from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0008_section'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='banner_text',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
    ]
