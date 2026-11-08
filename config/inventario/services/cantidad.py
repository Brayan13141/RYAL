"""Stock por cantidad de un TipoArticulo (gorras): se cuenta por unidades, no por par.

El stock es el último ajuste manual menos las ventas posteriores a él cuyo pedido no
esté cancelado. Cancelar un pedido o borrar su ítem devuelve las unidades solo.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from catalog.models import TipoArticulo
from inventario.models import MovimientoCantidad, Par
from negocio.models import Pedido

MOTIVO_ALTA = 'Alta de stock por cantidad'
VENTANA_BOT_MINUTOS = 15
_PREFIJO_ETIQUETA = 'TIPO.'
_AJUSTE = MovimientoCantidad.Clase.AJUSTE
_VENTA = MovimientoCantidad.Clase.VENTA


def _bloquear(tipo):
    return TipoArticulo.objects.select_for_update().get(pk=tipo.pk)


def stock_actual(tipo):
    ajuste = (MovimientoCantidad.objects.filter(tipo=tipo, clase=_AJUSTE)
              .order_by('-pk').first())
    if ajuste is None:
        return 0
    vendidas = (MovimientoCantidad.objects
                .filter(tipo=tipo, clase=_VENTA, pk__gt=ajuste.pk)
                .exclude(pedido_item__pedido__estado=Pedido.CANCELADO)
                .aggregate(total=Sum('unidades'))['total'] or 0)
    return ajuste.cantidad_nueva - vendidas


def activar(tipo, cantidad_inicial, *, usuario):
    if cantidad_inicial < 0:
        raise ValueError('La cantidad inicial no puede ser negativa.')
    with transaction.atomic():
        bloqueado = _bloquear(tipo)
        if bloqueado.stock_por_cantidad:
            raise ValueError('El tipo ya tiene stock por cantidad.')
        if Par.objects.filter(tipo=bloqueado).exists():
            raise ValueError('El tipo ya tiene pares registrados.')
        bloqueado.stock_por_cantidad = True
        try:
            bloqueado.full_clean()
        except ValidationError as e:
            raise ValueError('; '.join(e.messages)) from e
        bloqueado.save(update_fields=['stock_por_cantidad'])
        tipo.stock_por_cantidad = True
        return MovimientoCantidad.objects.create(
            tipo=bloqueado, clase=_AJUSTE, cantidad_anterior=0,
            cantidad_nueva=cantidad_inicial, motivo=MOTIVO_ALTA, usuario=usuario)


def ajustar(tipo, cantidad_nueva, *, motivo, usuario):
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValueError('El motivo es obligatorio.')
    if cantidad_nueva < 0:
        raise ValueError('La cantidad no puede ser negativa.')
    with transaction.atomic():
        bloqueado = _bloquear(tipo)
        if not bloqueado.stock_por_cantidad:
            raise ValueError('El tipo no tiene stock por cantidad.')
        return MovimientoCantidad.objects.create(
            tipo=bloqueado, clase=_AJUSTE, cantidad_anterior=stock_actual(bloqueado),
            cantidad_nueva=cantidad_nueva, motivo=motivo, usuario=usuario)


def registrar_salida(item, tipo, *, usuario=None):
    """Salida de stock de una venta. `usuario`: quien vendió (el bot no tiene)."""
    if not tipo.stock_por_cantidad:
        return None
    return MovimientoCantidad.objects.create(
        tipo=tipo, clase=_VENTA, unidades=item.cantidad, pedido_item=item,
        usuario=usuario)


def sincronizar_salida(item):
    MovimientoCantidad.objects.filter(pedido_item=item, clase=_VENTA).update(
        unidades=item.cantidad)


def codigo_etiqueta(tipo):
    return f'{_PREFIJO_ETIQUETA}{tipo.pk}'


def tipo_de_codigo(codigo):
    codigo = (codigo or '').strip().upper()
    if not codigo.startswith(_PREFIJO_ETIQUETA):
        return None
    resto = codigo[len(_PREFIJO_ETIQUETA):]
    # Más de 9 dígitos no cabe en un pk razonable y desbordaría el entero de la BD.
    if not (resto.isascii() and resto.isdigit()) or len(resto) > 9:
        return None
    return TipoArticulo.objects.filter(pk=int(resto), stock_por_cantidad=True).first()


def venta_bot_reciente(tipo):
    desde = timezone.now() - timedelta(minutes=VENTANA_BOT_MINUTOS)
    return (MovimientoCantidad.objects
            .filter(tipo=tipo, clase=_VENTA, pedido_item__sku_snapshot='TIENDA-BOT',
                    creado_at__gte=desde)
            .exclude(pedido_item__pedido__estado=Pedido.CANCELADO)
            .exists())
