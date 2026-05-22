from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0014_category_min_qty_per_item'),
    ]

    operations = [
        migrations.RenameField(
            model_name='volumetier',
            old_name='unit_price',
            new_name='discount_amount',
        ),
        migrations.AlterField(
            model_name='volumetier',
            name='discount_amount',
            field=models.DecimalField(
                decimal_places=2,
                max_digits=8,
                help_text='Descuento en MXN que se resta al precio final de cada producto',
            ),
        ),
    ]
