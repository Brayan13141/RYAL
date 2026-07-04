from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0009_order_descuento_aplicado'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='cost_snapshot',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Costo (base + envío) al momento del pedido.',
                max_digits=8,
                null=True,
            ),
        ),
    ]
