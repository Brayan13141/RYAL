from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0005_siteconfig_hero_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='HeroSlide',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='hero/')),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Slide del Hero',
                'verbose_name_plural': 'Slides del Hero',
                'ordering': ['display_order'],
            },
        ),
    ]
