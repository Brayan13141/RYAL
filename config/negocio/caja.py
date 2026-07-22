"""Cálculo del saldo real de caja, única fuente de verdad.

saldo = todo lo cobrado (OrderPayment web + Pago negocio, sin filtro de fecha)
        − todos los gastos
        + Σ ajustes manuales (arqueo / saldo inicial)

El dinero se arrastra mes a mes; el saldo inicial (efectivo previo a registrar
ventas) entra como el primer AjusteCaja. Lo usan el dashboard del panel y la
página de caja de negocio.
"""
from decimal import Decimal

from django.db.models import Sum

from orders.models import OrderPayment
from .models import Pago, Gasto, AjusteCaja


def caja_totales():
    """Devuelve dict con Decimals: cobrado, gastos, ajustes, saldo."""
    cobrado = ((OrderPayment.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0'))
               + (Pago.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')))
    gastos = Gasto.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    ajustes = AjusteCaja.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    return {
        'cobrado': cobrado,
        'gastos': gastos,
        'ajustes': ajustes,
        'saldo': cobrado - gastos + ajustes,
    }
