from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0010_product_price_override'),
    ]

    operations = [
        migrations.CreateModel(
            name='VolumeTier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('min_qty', models.PositiveIntegerField(help_text='Cantidad mínima para activar este descuento')),
                ('discount_pct', models.DecimalField(decimal_places=2, max_digits=5,
                                                     help_text='% de descuento sobre el precio final (ej: 10.00 = 10 %)')),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                               related_name='volume_tiers', to='catalog.category')),
            ],
            options={
                'verbose_name': 'Tier de volumen',
                'verbose_name_plural': 'Tiers de volumen',
                'ordering': ['min_qty'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='volumetier',
            unique_together={('category', 'min_qty')},
        ),
    ]
