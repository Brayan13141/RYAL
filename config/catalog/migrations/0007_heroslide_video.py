from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0006_heroslide'),
    ]

    operations = [
        migrations.AddField(
            model_name='heroslide',
            name='media_type',
            field=models.CharField(
                choices=[('image', 'Imagen'), ('video', 'Video')],
                default='image',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='heroslide',
            name='video',
            field=models.FileField(blank=True, upload_to='hero/videos/'),
        ),
        migrations.AlterField(
            model_name='heroslide',
            name='image',
            field=models.ImageField(blank=True, upload_to='hero/'),
        ),
    ]
