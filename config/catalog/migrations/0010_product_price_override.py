from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0009_category_banner_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='price_override',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Precio final fijo. Cuando se especifica ignora base_price, envío y margen.',
                max_digits=8,
                null=True,
            ),
        ),
    ]
