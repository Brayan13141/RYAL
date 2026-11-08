"""Ventas de la tienda web agregadas por producto (solo lectura).

El dinero sale de los snapshots grabados en cada renglón (`price_snapshot`) y
del costo con la misma cadena de respaldo que usa la caja
(`OrderItem.costo_con_fuente`), para que estas cifras cuadren con ella.
El universo son los pedidos que no están cancelados.
"""
from decimal import Decimal

from django.utils import timezone

from orders.models import Order

CENTAVO = Decimal('0.01')
ORDENES = ('ganancia', 'ingreso', 'unidades', 'ultima_venta', 'nombre')


def repartir_descuento(descuento, subtotales):
    """Reparte `descuento` a prorrata de `subtotales`, cuadrando al centavo.

    Cada parte se redondea a centavos y el último renglón absorbe el resto.
    Descuento 0 o suma de subtotales 0 → todo ceros. Mismo algoritmo que el
    inline de `negocio.services.ranking_por_tipo`.
    """
    bruto = sum(subtotales, Decimal('0'))
    if not descuento or not bruto:
        return [Decimal('0') for _ in subtotales]
    partes = []
    repartido = Decimal('0')
    for pos, subtotal in enumerate(subtotales):
        if pos == len(subtotales) - 1:
            parte = descuento - repartido
        else:
            parte = (descuento * subtotal / bruto).quantize(CENTAVO)
            repartido += parte
        partes.append(parte)
    return partes


def _pedidos(desde, hasta):
    """Pedidos no cancelados con sus renglones y productos precargados.

    El rango se filtra sobre la fecha LOCAL en Python (no en SQL) para que un
    pedido de las 23:30 en México no caiga en el día siguiente por ser UTC.
    """
    pedidos = Order.objects.exclude(status='cancelled').prefetch_related('items__product__category__parent')
    for pedido in pedidos.order_by('created_at', 'pk'):
        fecha = timezone.localtime(pedido.created_at).date()
        if desde is not None and fecha < desde:
            continue
        if hasta is not None and fecha > hasta:
            continue
        yield pedido, fecha


def lineas_venta_web(desde=None, hasta=None):
    """Un dict por renglón vendido, con el descuento del pedido ya repartido.

    `desde`/`hasta` son fechas locales, inclusivas.
    """
    for pedido, fecha in _pedidos(desde, hasta):
        items = sorted(pedido.items.all(), key=lambda i: i.pk)
        partes = repartir_descuento(
            pedido.descuento_aplicado, [i.subtotal for i in items])
        for item, parte in zip(items, partes):
            producto = item.product
            yield {
                'order': pedido,
                'item': item,
                'product_pk': item.product_id,
                'sku': producto.sku if producto else item.sku_snapshot,
                'nombre': producto.name if producto else item.name_snapshot,
                'unidades': item.quantity,
                'ingreso': item.subtotal - parte,
                'costo': item.costo_con_fuente()[0],
                'fecha': fecha,
            }


def ventas_web_por_producto(desde=None, hasta=None, orden='ganancia'):
    """Ventas agrupadas por producto; un producto borrado se agrupa por SKU.

    Órdenes válidos: ganancia, ingreso, unidades, ultima_venta (de mayor a
    menor) y nombre (A-Z). Uno inválido cae a ganancia.
    """
    if orden not in ORDENES:
        orden = 'ganancia'
    grupos = {}
    for linea in lineas_venta_web(desde, hasta):
        pk = linea['product_pk']
        clave = ('p', pk) if pk is not None else ('s', linea['sku'])
        fila = grupos.get(clave)
        if fila is None:
            fila = grupos[clave] = {
                'product_pk': pk, 'sku': linea['sku'], 'nombre': linea['nombre'],
                'borrado': pk is None, 'unidades': 0, 'ingreso': Decimal('0'),
                'costo': Decimal('0'), 'ganancia': Decimal('0'),
                'ultima_venta': linea['fecha'],
            }
        fila['unidades'] += linea['unidades']
        fila['ingreso'] += linea['ingreso']
        fila['costo'] += linea['costo']
        fila['ganancia'] += linea['ingreso'] - linea['costo']
        if linea['fecha'] > fila['ultima_venta']:
            fila['ultima_venta'] = linea['fecha']

    filas = list(grupos.values())
    if orden == 'nombre':
        filas.sort(key=lambda f: (f['nombre'].lower(), f['sku']))
    else:
        filas.sort(key=lambda f: f[orden], reverse=True)
    return filas


def historial_producto_web(product_pk=None, sku=None):
    """Renglones de un producto (o de un SKU borrado), más recientes primero."""
    historial = []
    for linea in lineas_venta_web():
        if product_pk is not None:
            if linea['product_pk'] != product_pk:
                continue
        elif linea['product_pk'] is not None or linea['sku'] != sku:
            continue
        linea['ganancia'] = linea['ingreso'] - linea['costo']
        historial.append(linea)
    historial.sort(key=lambda l: (l['fecha'], l['order'].pk, l['item'].pk), reverse=True)
    return historial
