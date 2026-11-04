from decimal import Decimal as D

from django.db import migrations

# (eu, us_hombre, us_mujer, mx_cm, cn_mm); orden = posición. 25.5 y 27.5 sin equivalencias.
TALLAS = [
    (35, None, 5, '22', 220),
    (36, 4, 6, '23', 230),
    (37, 5, 6.5, '23.5', 235),
    (38, 6, 7.5, '24', 240),
    (39, 7, 8, '24.5', 245),
    (40, 7.5, 9, '25', 250),
    (None, None, None, '25.5', None),
    (41, 8.5, 10, '26', 260),
    (42, 9, 10.5, '26.5', 265),
    (43, 10, 11.5, '27', 270),
    (None, None, None, '27.5', None),
    (44, 11, 12, '28', 280),
    (45, 12, None, '29', 290),
]
TARIFAS = [('aereo', '280'), ('maritimo', '180')]


def _d(valor):
    return None if valor is None else D(str(valor))


def sembrar(apps, schema_editor):
    Talla = apps.get_model('inventario', 'TallaCalzado')
    Tarifa = apps.get_model('inventario', 'TarifaFlete')
    for orden, (eu, us_h, us_m, mx, cn) in enumerate(TALLAS, start=1):
        Talla.objects.update_or_create(mx_cm=D(mx), defaults={
            'eu': _d(eu), 'us_hombre': _d(us_h), 'us_mujer': _d(us_m),
            'cn_mm': cn, 'orden': orden,
        })
    for via, monto in TARIFAS:
        Tarifa.objects.update_or_create(via=via, defaults={'monto_por_par': D(monto)})


def deshacer(apps, schema_editor):
    apps.get_model('inventario', 'TallaCalzado').objects.filter(
        mx_cm__in=[D(t[3]) for t in TALLAS]).delete()
    apps.get_model('inventario', 'TarifaFlete').objects.filter(
        via__in=[v for v, _ in TARIFAS]).delete()


class Migration(migrations.Migration):

    dependencies = [('inventario', '0001_initial')]

    operations = [migrations.RunPython(sembrar, deshacer)]
