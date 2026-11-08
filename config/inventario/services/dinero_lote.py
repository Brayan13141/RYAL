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


def dinero_del_lote(lote):
    """Totales del lote y sus pares vendidos agrupados por modelo y vía.

    Devuelve `{'cobrado', 'costo', 'falta', 'ganancia', 'vendidos', 'sin_precio', 'grupos'}`; cada
    grupo es `{'modelo', 'via', 'pares', 'n', 'cobrado', 'costo'}` y cada par
    `{'par', 'precio', 'pedido_id', 'fecha', 'talla'}`, del más reciente al más viejo. `precio` es
    None si el par vendido no está ligado a ningún ítem (cuenta $0 y se avisa).
    """
    vigentes = lote.pares.exclude(estado=Par.Estado.ANULADO)
    costo = sum((c for c in vigentes.values_list('costo', flat=True)), Decimal('0'))

    vendidos = (vigentes.filter(estado=Par.Estado.VENDIDO)
                .select_related('tipo__padre', 'color', 'talla', 'pedido_item')
                .order_by('-vendido_at', '-pk'))
    grupos, cobrado, sin_precio = {}, Decimal('0'), 0
    for par in vendidos:
        item = par.pedido_item
        precio = item.precio_unitario if item is not None else None
        if precio is None:
            sin_precio += 1
        else:
            cobrado += precio
        g = grupos.setdefault(par.tipo_id, {
            'modelo': par.tipo.modelo_base.nombre, 'via': par.tipo.get_via_display() or '',
            'pares': [], 'n': 0, 'cobrado': Decimal('0'), 'costo': Decimal('0')})
        g['pares'].append({'par': par, 'precio': precio, 'fecha': par.vendido_at,
                           'talla': fmt_talla(par.talla.mx_cm),
                           'pedido_id': item.pedido_id if item is not None else None})
        g['n'] += 1
        g['cobrado'] += precio or Decimal('0')
        g['costo'] += par.costo

    return {
        'cobrado': cobrado, 'costo': costo, 'falta': costo - cobrado,
        'ganancia': max(cobrado - costo, Decimal('0')),
        'vendidos': sum(g['n'] for g in grupos.values()), 'sin_precio': sin_precio,
        'grupos': sorted(grupos.values(), key=lambda g: (g['modelo'], g['via'])),
    }
