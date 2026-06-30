from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0028_tipo_articulo_codigo_descuento'),
    ]

    operations = [
        migrations.AddField(
            model_name='codigodescuento',
            name='canal',
            field=models.CharField(
                choices=[('ambos', 'Ambos (negocio + web)'), ('negocio', 'Solo negocio (bot / POS)'), ('web', 'Solo web (ryalsneackers.com)')],
                default='ambos',
                help_text='Dónde puede usarse este código.',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='codigodescuento',
            name='categoria_web',
            field=models.ForeignKey(
                blank=True,
                help_text='(Web) Dejar vacío para código global. Aplica solo a productos de esta categoría.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='codigos_descuento',
                to='catalog.category',
            ),
        ),
    ]
