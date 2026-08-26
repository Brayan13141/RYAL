"""Cálculo del saldo real de caja, única fuente de verdad.

saldo = todo lo cobrado (OrderPayment web + Pago negocio, sin filtro de fecha)
        − todos los gastos
        − la mercancía de los pedidos web ya cobrados
        + Σ ajustes manuales (arqueo / saldo inicial)

Lo de la mercancía web: un pedido de la tienda online entra completo a caja,
pero al proveedor todavía hay que pagarle, y esa salida NO queda registrada en
ningún lado — la app `orders` no crea Gastos. Sin descontarla, la caja muestra
como propio dinero que ya es del proveedor. En el negocio no pasa: ahí la
compra al proveedor se carga como Gasto, así que descontarla acá la restaría
dos veces.

El dinero se arrastra mes a mes; el saldo inicial (efectivo previo a registrar
ventas) entra como el primer AjusteCaja. Lo usan el dashboard del panel y la
página de caja de negocio.
"""
from decimal import Decimal

from django.db.models import Sum

from orders.models import Order, OrderPayment
from .models import Pago, Gasto, AjusteCaja


def costo_mercancia_web():
    """Costo de la mercancía de los pedidos web que ya se cobraron.

    Basta un anticipo para que cuente completo: al proveedor se le paga el
    pedido entero, no la parte que el cliente adelantó.
    """
    cobrados = set(OrderPayment.objects.values_list('order_id', flat=True))
    if not cobrados:
        return Decimal('0')
    pedidos = Order.objects.filter(pk__in=cobrados).prefetch_related('items__product')
    return sum((p.costo_mercancia for p in pedidos), Decimal('0'))


def caja_totales():
    """Devuelve dict con Decimals: cobrado, gastos, costo_mercancia_web,
    ajustes, saldo."""
    cobrado = ((OrderPayment.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0'))
               + (Pago.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')))
    gastos = Gasto.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    mercancia = costo_mercancia_web()
    ajustes = AjusteCaja.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    return {
        'cobrado': cobrado,
        'gastos': gastos,
        'costo_mercancia_web': mercancia,
        'ajustes': ajustes,
        'saldo': cobrado - gastos - mercancia + ajustes,
    }
