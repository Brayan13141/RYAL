import datetime

from django.db.models import DecimalField, ExpressionWrapper, F

_MESES_ES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

_GANANCIA_EXPR = ExpressionWrapper(
    F('precio_venta') - F('costo_producto') - F('descuento_aplicado'),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


def _mes_range(year, month):
    """Devuelve (fecha_ini, fecha_fin) para el mes dado."""
    ini = datetime.date(year, month, 1)
    fin = datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)
    return ini, fin
