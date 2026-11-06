from django.db import migrations


def mover_colores_al_base(apps, schema_editor):
    """Los colores se leen solo del modelo base (colores_efectivos). Los que se
    marcaron en una variante desde Tipos quedaban invisibles en el alta, el
    import y el bot: se suman al base y la variante queda sin colores propios."""
    TipoArticulo = apps.get_model('catalog', 'TipoArticulo')
    for variante in TipoArticulo.objects.filter(padre__isnull=False).prefetch_related('colores'):
        colores = list(variante.colores.all())
        if colores:
            variante.padre.colores.add(*colores)
            variante.colores.clear()


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0039_alter_tipoarticulo_via'),
    ]

    operations = [
        migrations.RunPython(mover_colores_al_base, migrations.RunPython.noop),
    ]
