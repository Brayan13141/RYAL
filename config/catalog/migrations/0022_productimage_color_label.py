from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0021_product_color_variants_size_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='color_label',
            field=models.CharField(
                blank=True,
                max_length=60,
                help_text='Nombre visible del color en el carrito (ej. "Blanco", "Negro/Rojo"). Solo relevante en modo colorway.',
            ),
        ),
    ]
