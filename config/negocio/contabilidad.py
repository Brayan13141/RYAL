"""Libro de ingresos y egresos para el contador.

Base de efectivo: cada movimiento se fecha cuando se movió el dinero.

- Ingresos: todo lo cobrado, del negocio (`Pago`) y de la web (`OrderPayment`,
  BRUTO: el contador ve el cobro completo, no solo la ganancia como la caja).
  Los cobros de pedidos cancelados entran marcados: no hay registro de
  devoluciones, ocultarlos haría desaparecer dinero que sí entró.
- Egresos: los `Gasto` y el costo de proveedor de los renglones web que se
  compraron de verdad (`SupplierOrderItem.status == 'added'`), fechado en el
  día —hora de México— en que se armó el pedido al proveedor.

Fuera a propósito: `Pedido.costo_producto` (la compra del negocio ya es un
`Gasto` "Compra al proveedor"; contarla también la duplicaría, el error que
se arregló en `ganancia_neta`) y `AjusteCaja` (un cuadre, no un movimiento).
"""
import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from orders.models import FUENTE_REGISTRADO, Order, OrderItem, OrderPayment
from .models import Gasto, Pago, Pedido
from .utils import _MESES_ES


@dataclass(frozen=True)
class Movimiento:
    fecha: datetime.date
    tipo: str            # 'ingreso' | 'egreso'
    origen: str          # origen del cobro o categoría del egreso
    referencia: str
    concepto: str
    monto: Decimal
    metodo: str = ''
    cancelado: bool = False
    fuente_costo: str = ''
    estimado: bool = False


def _clave(mov):
    """`Pedido #9` antes que `Pedido #10`: el número se compara como número."""
    prefijo, _, numero = mov.referencia.rpartition('#')
    if prefijo and numero.isdigit():
        return (mov.fecha, prefijo, int(numero), '')
    return (mov.fecha, mov.referencia, 0, mov.concepto)


def _ordenar(movs):
    return sorted(movs, key=_clave)


def _aware(fecha):
    """Medianoche de `fecha` en la zona del proyecto (México)."""
    return timezone.make_aware(datetime.datetime.combine(fecha, datetime.time.min))


def ingresos(desde, hasta):
    movs = []
    pagos = Pago.objects.filter(fecha__gte=desde, fecha__lt=hasta).select_related('pedido__cliente')
    for pago in pagos:
        pedido = pago.pedido
        movs.append(Movimiento(
            fecha=pago.fecha, tipo='ingreso', origen=pedido.get_origen_display(),
            referencia=f'Pedido #{pedido.pk}',
            concepto=pedido.cliente.nombre if pedido.cliente_id else '',
            monto=pago.monto, metodo=pago.get_metodo_pago_display(),
            cancelado=pedido.estado == Pedido.CANCELADO,
        ))
    cobros_web = OrderPayment.objects.filter(
        fecha__gte=desde, fecha__lt=hasta).select_related('order')
    for pago in cobros_web:
        order = pago.order
        movs.append(Movimiento(
            fecha=pago.fecha, tipo='ingreso', origen='Web',
            referencia=f'Web {order.order_code}', concepto=order.customer_name,
            monto=pago.monto, metodo=pago.get_metodo_pago_display(),
            cancelado=order.status == 'cancelled',
        ))
    return _ordenar(movs)


def egresos(desde, hasta):
    movs = [
        Movimiento(
            fecha=g.fecha, tipo='egreso', origen=g.get_categoria_display(),
            referencia=f'Gasto #{g.pk}', concepto=g.descripcion, monto=g.monto,
        )
        for g in Gasto.objects.filter(fecha__gte=desde, fecha__lt=hasta)
    ]
    comprados = (
        OrderItem.objects
        .filter(
            supplier_item__status='added',
            order__supplier_order__created_at__gte=_aware(desde),
            order__supplier_order__created_at__lt=_aware(hasta),
        )
        .distinct()   # un renglón con dos `added` (reintentos) cuenta una vez
        .select_related('order__supplier_order', 'product__category__parent')
    )
    for item in comprados:
        costo, fuente = item.costo_con_fuente()
        order = item.order
        movs.append(Movimiento(
            fecha=timezone.localtime(order.supplier_order.created_at).date(),
            tipo='egreso', origen='Costo proveedor web',
            referencia=f'Web {order.order_code}',
            concepto=f'{item.sku_snapshot} — {item.name_snapshot}',
            monto=costo, cancelado=order.status == 'cancelled',
            fuente_costo=fuente, estimado=fuente != FUENTE_REGISTRADO,
        ))
    return _ordenar(movs)


def _meses(desde, hasta):
    y, m = desde.year, desde.month
    while datetime.date(y, m, 1) < hasta:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def resumen_mensual(desde, hasta):
    """Una fila por mes del rango, también los meses en cero: para el contador
    un mes vacío también es un dato."""
    totales = {ym: [Decimal('0'), Decimal('0')] for ym in _meses(desde, hasta)}
    for mov in ingresos(desde, hasta):
        totales[(mov.fecha.year, mov.fecha.month)][0] += mov.monto
    for mov in egresos(desde, hasta):
        totales[(mov.fecha.year, mov.fecha.month)][1] += mov.monto
    return [
        {'mes': f'{_MESES_ES[m - 1]} {y}', 'ingresos': ing, 'egresos': egr,
         'diferencia': ing - egr}
        for (y, m), (ing, egr) in totales.items()
    ]


def pendientes_de_costo(desde, hasta):
    """Pedidos web cobrados en el rango con algún renglón que todavía no se le
    compró al proveedor: su costo está por llegar a algún mes."""
    cobrados = (Order.objects
                .filter(payments__fecha__gte=desde, payments__fecha__lt=hasta)
                .exclude(status='cancelled'))
    return (OrderItem.objects
            .filter(order__in=cobrados)
            .exclude(supplier_item__status='added')
            .values('order').distinct().count())
