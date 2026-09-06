import datetime

from django.db.models import DecimalField, ExpressionWrapper, F

_MESES_ES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

_GANANCIA_EXPR = ExpressionWrapper(
    F('precio_venta') - F('costo_producto') - F('descuento_aplicado'),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)

# Ingreso NETO de descuentos — mismo criterio que el dashboard (_stats_pedido).
# Todos los reportes deben sumar esta expresión, nunca precio_venta bruto.
_VENDIDO_EXPR = ExpressionWrapper(
    F('precio_venta') - F('descuento_aplicado'),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


def _mes_range(year, month):
    """Devuelve (fecha_ini, fecha_fin) para el mes dado."""
    ini = datetime.date(year, month, 1)
    fin = datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)
    return ini, fin


def _mes_previo_comparable(year, month, hoy):
    """Ventana del mes anterior contra la que comparar (year, month).

    Devuelve `(ini, fin, label)` con `fin` exclusivo, igual que `_mes_range`.

    Si el mes pedido es el que está en curso, la ventana previa se recorta al
    MISMO día: cinco días de septiembre contra un agosto completo pintan a
    todo el catálogo en caída, y ahí el número miente por el tamaño de la
    ventana, no por las ventas. El `label` dice cuál de las dos ventanas es
    para que la pantalla no tenga que adivinarlo.
    """
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    ini, fin = _mes_range(py, pm)
    label = _MESES_ES[pm - 1]

    if (year, month) == (hoy.year, hoy.month):
        # `hoy.day` días desde el 1º, no `hoy.day - 1`: `fin` es exclusivo y el
        # día en curso cuenta completo de los dos lados. Nunca invade el mes
        # siguiente — un 31 contra febrero se queda en el fin de febrero.
        recorte = min(ini + datetime.timedelta(days=hoy.day), fin)
        if recorte < fin:
            fin = recorte
            label = f"1-{hoy.day} {label}"

    return ini, fin, label
