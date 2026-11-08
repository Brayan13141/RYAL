"""Dinero de un lote: lo cobrado por sus pares vendidos contra lo que costó el lote.

- **Cobrado**: el precio de venta de cada par vendido ahora mismo (el de su ítem, venga
  del escáner o del bot). Es lo que debería haber en caja por este lote.
- **Costo**: el costo de todos sus pares salvo los anulados (etiquetas capturadas por
  error). Las bajas sí cuentan: esa mercancía se pagó.
- **Falta**: costo − cobrado. Negativo = el lote ya dio ganancia.

Un par cuya venta se canceló vuelve a stock, así que deja de contar solo.
"""
from decimal import Decimal

from inventario.models import Par, fmt_talla

CERO = Decimal('0')


def _vigentes(lote):
    return lote.pares.exclude(estado=Par.Estado.ANULADO)


def _opciones(vendidos):
    """Modelos, colores y tallas que existen entre los vendidos del lote (para los filtros)."""
    modelos, colores, tallas = {}, {}, {}
    for par in vendidos:
        via = par.tipo.get_via_display()
        modelos[par.tipo_id] = par.tipo.modelo_base.nombre + (f' · {via}' if via else '')
        colores[par.color_id] = par.color.nombre
        tallas[par.talla_id] = (par.talla.orden, fmt_talla(par.talla.mx_cm))
    return {
        'modelos': sorted(modelos.items(), key=lambda x: x[1]),
        'colores': sorted(colores.items(), key=lambda x: x[1]),
        'tallas': [(pk, t[1]) for pk, t in sorted(tallas.items(), key=lambda x: x[1])],
    }


def vendidos_del_lote(lote, *, tipo_id=None, color_id=None, talla_id=None,
                      desde=None, hasta=None):
    """Pares vendidos del lote, del más reciente al más viejo, con sus totales.

    Filtros opcionales: `tipo_id` (modelo y vía), `color_id`, `talla_id` y un rango de
    fechas locales de venta `desde`/`hasta` (date, inclusivos). Las `opciones` salen de
    TODOS los vendidos del lote, no de los filtrados, para poder cambiar de filtro.

    Devuelve `{'filas', 'n', 'cobrado', 'costo', 'margen', 'sin_precio', 'opciones'}`;
    cada fila es `{'par', 'modelo', 'via', 'talla', 'fecha', 'precio', 'pedido_id'}`.
    `precio` es None si el par vendido no está ligado a ningún ítem (cuenta $0).
    """
    todos = (_vigentes(lote).filter(estado=Par.Estado.VENDIDO)
             .select_related('tipo__padre', 'color', 'talla', 'pedido_item')
             .order_by('-vendido_at', '-pk'))
    opciones = _opciones(todos)
    qs = todos
    if tipo_id is not None:
        qs = qs.filter(tipo_id=tipo_id)
    if color_id is not None:
        qs = qs.filter(color_id=color_id)
    if talla_id is not None:
        qs = qs.filter(talla_id=talla_id)
    if desde is not None:
        qs = qs.filter(vendido_at__date__gte=desde)
    if hasta is not None:
        qs = qs.filter(vendido_at__date__lte=hasta)

    filas, cobrado, costo, sin_precio = [], CERO, CERO, 0
    for par in qs:
        item = par.pedido_item
        precio = item.precio_unitario if item is not None else None
        if precio is None:
            sin_precio += 1
        else:
            cobrado += precio
        costo += par.costo
        filas.append({
            'par': par, 'modelo': par.tipo.modelo_base.nombre,
            'via': par.tipo.get_via_display() or '', 'talla': fmt_talla(par.talla.mx_cm),
            'fecha': par.vendido_at, 'precio': precio,
            'pedido_id': item.pedido_id if item is not None else None,
        })
    return {'filas': filas, 'n': len(filas), 'cobrado': cobrado, 'costo': costo,
            'margen': cobrado - costo, 'sin_precio': sin_precio, 'opciones': opciones}


def dinero_del_lote(lote):
    """Totales del lote: `{'cobrado', 'costo', 'falta', 'ganancia', 'vendidos', 'sin_precio'}`."""
    costo = sum(_vigentes(lote).values_list('costo', flat=True), CERO)
    v = vendidos_del_lote(lote)
    return {
        'cobrado': v['cobrado'], 'costo': costo, 'falta': costo - v['cobrado'],
        'ganancia': max(v['cobrado'] - costo, CERO),
        'vendidos': v['n'], 'sin_precio': v['sin_precio'],
    }
