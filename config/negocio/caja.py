"""Cálculo del saldo real de caja, única fuente de verdad.

saldo = lo CONTADO en el último arqueo (`AjusteCaja.saldo_resultante`)
        + cobrado del negocio después del arqueo
        + lo aportado por los pedidos web después del arqueo
        − gastos después del arqueo

Sin arqueo, la base es 0 y se suma todo.

**El arqueo manda.** Un `AjusteCaja` afirma "conté el efectivo y hay esto". Lo
anterior queda cuadrado por definición, con sus errores ya absorbidos, así que
no se vuelve a calcular: ni una regla nueva (el error que hundió la caja de
$27,258 a $20,553 el 2026-08-25) ni el propio arqueo pueden reescribirlo.

Antes se sumaban los `monto` de todos los ajustes. Cada `monto` se había
calculado con el corte vigente en su momento, pero guardar el arqueo movía el
corte, y los pedidos web cobrados entre el arqueo anterior y el nuevo pasaban
de aportar su ganancia a aportar el cobro completo. El 2026-11-04 la caja
marcaba $25,090 cuando dos días antes se habían contado $9,100.

**Después del arqueo** = fecha posterior, o del mismo día pero registrado
después del arqueo (`created_at`). Lo grabado antes de que existiera
`created_at` no tiene hora: si es del día del arqueo se asume que ya estaba en
el conteo.

**Los pedidos web aportan solo su GANANCIA, y solo cuando están liquidados.**
El resto del cobro es dinero que se le debe al proveedor: entra a la caja
física, sí, pero no es tuyo. Mientras el pedido no esté 100% cubierto no se
reconoce nada — ni siquiera la parte proporcional del anticipo, porque el
anticipo es justo lo primero que se va en pagar la mercancía.

En el NEGOCIO no aplica: ahí la compra al proveedor se carga como `Gasto`, así
que el costo ya sale por su lado y descontarlo también acá lo restaría dos
veces.
"""
from decimal import Decimal

from django.db.models import Q, Sum

from orders.models import Order, OrderPayment
from .models import Pago, Gasto, AjusteCaja


def ultimo_arqueo():
    """Hasta acá la caja ya se cuadró contra el efectivo real."""
    return AjusteCaja.objects.order_by('-fecha', '-created_at', '-id').first()


def _posteriores(qs, arqueo):
    """Movimientos que el conteo del arqueo todavía no incluía."""
    if arqueo is None:
        return qs
    return qs.filter(
        Q(fecha__gt=arqueo.fecha)
        | Q(fecha=arqueo.fecha, created_at__gt=arqueo.created_at))


def _suma(qs):
    return qs.aggregate(t=Sum('monto'))['t'] or Decimal('0')


def aporte_web(arqueo=None):
    """Lo que los pedidos web le suman a la caja después del arqueo.

    Devuelve (ganancia_reconocida, cobrado_bruto).
    """
    pagos = _posteriores(OrderPayment.objects.all(), arqueo)
    bruto = _suma(pagos)

    ganancia = Decimal('0')
    pedidos = Order.objects.filter(
        pk__in=pagos.values_list('order_id', flat=True)
    ).prefetch_related('items__product', 'payments')
    for pedido in pedidos:
        pagado = sum((p.monto for p in pedido.payments.all()), Decimal('0'))
        # Liquidado o nada: un anticipo es lo primero que se va en la mercancía.
        if pagado >= pedido.total:
            ganancia += pedido.ganancia

    return ganancia, bruto


def caja_totales():
    """Devuelve dict con: base (lo contado en el último arqueo), corte (su
    fecha o None), cobrado, gastos, saldo — cobrado y gastos son solo lo
    posterior al arqueo, de modo que base + cobrado − gastos = saldo —, el
    desglose web (cobrado_web_bruto, ganancia_web) y ajustes (Σ histórica de
    los ajustes, informativa: ya no entra al saldo)."""
    arqueo = ultimo_arqueo()
    base = arqueo.saldo_resultante if arqueo else Decimal('0')

    ganancia_web, bruto_web = aporte_web(arqueo)
    cobrado_negocio = _suma(_posteriores(Pago.objects.all(), arqueo))
    gastos = _suma(_posteriores(Gasto.objects.all(), arqueo))
    cobrado = cobrado_negocio + ganancia_web

    return {
        'base': base,
        'corte': arqueo.fecha if arqueo else None,
        'cobrado': cobrado,
        'cobrado_web_bruto': bruto_web,
        'ganancia_web': ganancia_web,
        'gastos': gastos,
        'ajustes': _suma(AjusteCaja.objects.all()),
        'saldo': base + cobrado - gastos,
    }
