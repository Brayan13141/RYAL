from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0008_supplierorder_cart_script'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='descuento_aplicado',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=8),
        ),
    ]
