"""Cálculo del saldo real de caja, única fuente de verdad.

saldo = cobrado del negocio
        + lo aportado por los pedidos web
        − todos los gastos
        + Σ ajustes manuales (arqueo / saldo inicial)

**Los pedidos web aportan solo su GANANCIA, y solo cuando están liquidados.**
El resto del cobro es dinero que se le debe al proveedor: entra a la caja
física, sí, pero no es tuyo. Mientras el pedido no esté 100% cubierto no se
reconoce nada — ni siquiera la parte proporcional del anticipo, porque el
anticipo es justo lo primero que se va en pagar la mercancía.

En el NEGOCIO no aplica: ahí la compra al proveedor se carga como `Gasto`, así
que el costo ya sale por su lado y descontarlo también acá lo restaría dos
veces.

**El corte del arqueo.** Un `AjusteCaja` declara "el efectivo real es este":
todo lo cobrado hasta esa fecha ya se cuadró contra dinero contado a mano, con
sus compras al proveedor incluidas. Aplicarle la regla nueva restaría esa
mercancía por segunda vez — el error que hundió la caja de $27,258 a $20,553 el
2026-08-25. Por eso los cobros anteriores al último arqueo se toman tal cual.
"""
from decimal import Decimal

from django.db.models import Sum

from orders.models import Order, OrderPayment
from .models import Pago, Gasto, AjusteCaja


def _fecha_ultimo_arqueo():
    """Hasta acá la caja ya se cuadró contra el efectivo real."""
    ultimo = AjusteCaja.objects.order_by('-fecha').first()
    return ultimo.fecha if ultimo else None


def aporte_web():
    """Lo que los pedidos web le suman a la caja.

    Devuelve (aporte, ganancia_reconocida, cobrado_bruto).
    """
    corte = _fecha_ultimo_arqueo()

    recientes = set(
        OrderPayment.objects
        .filter(fecha__gt=corte).values_list('order_id', flat=True)
    ) if corte else set(OrderPayment.objects.values_list('order_id', flat=True))

    previos = OrderPayment.objects.exclude(order_id__in=recientes)
    aporte = previos.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    bruto = OrderPayment.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')

    ganancia = Decimal('0')
    pedidos = Order.objects.filter(pk__in=recientes).prefetch_related(
        'items__product', 'payments')
    for pedido in pedidos:
        pagado = sum((p.monto for p in pedido.payments.all()), Decimal('0'))
        # Liquidado o nada: un anticipo es lo primero que se va en la mercancía.
        if pagado >= pedido.total:
            ganancia += pedido.ganancia

    return aporte + ganancia, ganancia, bruto


def caja_totales():
    """Devuelve dict con Decimals: cobrado, gastos, ajustes, saldo, y el
    desglose de lo web (cobrado_web_bruto, ganancia_web)."""
    aporte, ganancia_web, bruto_web = aporte_web()
    cobrado_negocio = Pago.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    gastos = Gasto.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    ajustes = AjusteCaja.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    return {
        'cobrado': cobrado_negocio + aporte,
        'cobrado_web_bruto': bruto_web,
        'ganancia_web': ganancia_web,
        'gastos': gastos,
        'ajustes': ajustes,
        'saldo': cobrado_negocio + aporte - gastos + ajustes,
    }
