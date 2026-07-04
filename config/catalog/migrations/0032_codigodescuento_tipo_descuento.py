from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0031_codigodescuento_codigo_blank'),
    ]

    operations = [
        migrations.AddField(
            model_name='codigodescuento',
            name='tipo_descuento',
            field=models.CharField(
                choices=[('fijo', 'Fijo — descuenta un monto fijo del total'), ('por_item', 'Por ítem — se multiplica por cada ítem del alcance')],
                default='fijo',
                help_text='Fijo: descuenta el monto sin importar cuántos ítems. Por ítem: multiplica el monto × cantidad de ítems del alcance.',
                max_length=10,
            ),
        ),
    ]
