"""`created_at` en Pago y Gasto, para desempatar contra el arqueo los
movimientos del mismo día.

Va en dos pasos a propósito: con un solo AddField que traiga
`default=timezone.now`, Django rellenaría las filas existentes con la hora de
la migración y todo lo histórico parecería registrado DESPUÉS del último
arqueo — sumándose dos veces a la caja. Primero se agrega nullable sin default
(lo viejo queda NULL = "sin hora, ya estaba en el conteo") y después se pone el
default, que solo aplica a filas nuevas.
"""
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('negocio', '0010_pedido_idem_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='pago',
            name='created_at',
            field=models.DateTimeField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='gasto',
            name='created_at',
            field=models.DateTimeField(editable=False, null=True),
        ),
        migrations.AlterField(
            model_name='pago',
            name='created_at',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False, null=True),
        ),
        migrations.AlterField(
            model_name='gasto',
            name='created_at',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False, null=True),
        ),
    ]
