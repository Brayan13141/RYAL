from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_add_track_message_to_siteconfig'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='hero_eyebrow',
            field=models.CharField(blank=True, default='Mayoreo · Importación directa · MX', help_text='Texto pequeño sobre el título del hero', max_length=120),
        ),
        migrations.AddField(
            model_name='siteconfig',
            name='hero_title_em',
            field=models.CharField(blank=True, default='Tu inventario,', help_text='Primera línea del título (cursiva dorada)', max_length=120),
        ),
        migrations.AddField(
            model_name='siteconfig',
            name='hero_title_strong',
            field=models.CharField(blank=True, default='siempre asegurado', help_text='Segunda línea del título (mayúsculas, grande)', max_length=120),
        ),
        migrations.AddField(
            model_name='siteconfig',
            name='hero_sub',
            field=models.TextField(blank=True, default='Sneakers y gorras directo de fábrica.\nDisponibilidad constante, mejor margen y cero improvisación.', help_text='Texto descriptivo debajo del título'),
        ),
        migrations.AddField(
            model_name='siteconfig',
            name='hero_stat_1_value',
            field=models.CharField(blank=True, default='+2K', max_length=20),
        ),
        migrations.AddField(
            model_name='siteconfig',
            name='hero_stat_1_label',
            field=models.CharField(blank=True, default='Productos', max_length=40),
        ),
        migrations.AddField(
            model_name='siteconfig',
            name='hero_stat_2_value',
            field=models.CharField(blank=True, default='100%', max_length=20),
        ),
        migrations.AddField(
            model_name='siteconfig',
            name='hero_stat_2_label',
            field=models.CharField(blank=True, default='Stock real', max_length=40),
        ),
    ]
