from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0020_category_has_color_variants'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='has_color_variants',
            field=models.BooleanField(
                default=False,
                help_text='Las imágenes representan colores/variantes distintas (sobreescribe el de la subcategoría)',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='size_group',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='products',
                to='catalog.sizegroup',
                help_text='Grupo de tallas personalizado (sobreescribe el de la subcategoría)',
            ),
        ),
    ]
