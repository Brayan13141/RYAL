from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0011_volumetier'),
    ]

    operations = [
        migrations.RenameField(
            model_name='volumetier',
            old_name='discount_pct',
            new_name='unit_price',
        ),
        migrations.AlterField(
            model_name='volumetier',
            name='unit_price',
            field=models.DecimalField(
                decimal_places=2,
                help_text='Precio por pieza en MXN (ya incluye envío y margen)',
                max_digits=8,
            ),
        ),
    ]
