from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0019_subcategorysection'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='has_color_variants',
            field=models.BooleanField(
                default=False,
                help_text='Las imágenes del producto representan variantes de color distintas (ej. calzado por colorway)',
            ),
        ),
    ]
