from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0013_alter_volumetier_min_qty'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='min_qty_per_item',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Mínimo de piezas por modelo en el carrito (0 = sin restricción por modelo, ej: calzado=12)',
            ),
        ),
    ]
