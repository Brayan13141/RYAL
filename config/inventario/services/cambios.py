"""Cambio de par: el cliente regresa un par vendido y se lleva otro en la misma venta.

Reglas del negocio (Bryan, 2026-11-06): se cobra la diferencia si el nuevo cuesta
más; nunca se devuelve dinero. El renglón de la venta conserva el precio pagado
cuando el nuevo es más barato, así el ingreso no baja.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from inventario.models import Par, fmt_talla
from inventario.services.ventas import _descripcion
from negocio.models import Pago, Pedido, PedidoItem

_METODOS_VALIDOS = {m[0] for m in Pago.METODO_CHOICES}


@dataclass
class ResultadoCambio:
    pedido: Pedido
    diferencia: Decimal


def _nombre(par):
    return f'{par.tipo.nombre} {par.color.nombre} {fmt_talla(par.talla.mx_cm)}'


def cambiar_par(regresa, nuevo, *, precio, metodo_pago, usuario, danado=False, nota=''):
    """Pasa la venta de `regresa` a `nuevo`. Todo o nada: ValueError y no cambia nada.

    `precio`: precio del par nuevo. Si supera lo pagado por el regresado, la
    diferencia se cobra con `metodo_pago` en la misma venta.
    `danado`: el regresado queda de baja por dañado en vez de volver a stock.
    """
    nota = (nota or '').strip()
    if not isinstance(precio, Decimal) or not precio.is_finite() or precio < 0:
        raise ValueError('Precio inválido.')
    if regresa.pk == nuevo.pk:
        raise ValueError('El par que se lleva tiene que ser otro.')

    with transaction.atomic():
        # Lectura sin candado solo para saber qué pedido bloquear; todo se revalida abajo.
        item_id = Par.objects.filter(pk=regresa.pk).values_list('pedido_item_id', flat=True).first()
        pedido_id = (PedidoItem.objects.filter(pk=item_id).values_list('pedido_id', flat=True)
                     .first() if item_id else None)
        if pedido_id is None:
            raise ValueError(f'El par {regresa.codigo} no está vendido en una venta.')
        # Orden de candados: Pedido -> ítem -> pares (igual que cancelar/quitar).
        pedido = Pedido.objects.select_for_update().get(pk=pedido_id)
        item = PedidoItem.objects.select_for_update().filter(pk=item_id).first()
        bloqueados = {p.pk: p for p in Par.objects.select_for_update(of=('self',))
                      .select_related('tipo', 'color', 'talla')
                      .filter(pk__in=[regresa.pk, nuevo.pk]).order_by('pk')}
        regresa, nuevo = bloqueados[regresa.pk], bloqueados[nuevo.pk]

        if (item is None or regresa.estado != Par.Estado.VENDIDO
                or regresa.pedido_item_id != item.pk):
            raise ValueError(f'El par {regresa.codigo} no está vendido en una venta.')
        if pedido.estado == Pedido.CANCELADO:
            raise ValueError('Esa venta está cancelada.')
        if nuevo.estado != Par.Estado.EN_STOCK:
            raise ValueError(f'El par {nuevo.codigo} no está en stock.')

        pagado = item.precio_unitario
        diferencia = max(Decimal('0'), precio - pagado)
        if diferencia > 0 and metodo_pago not in _METODOS_VALIDOS:
            raise ValueError('Elige el método de pago para cobrar la diferencia.')
        precio_final = max(precio, pagado)

        extra = f'. {nota}' if nota else ''
        regresa.transicionar('en_stock', usuario=usuario, origen='deshacer',
                             nota=f'Cambio: regresó por {nuevo.codigo}{extra}')
        if danado:
            regresa.transicionar('baja', usuario=usuario, origen='manual', motivo='danado',
                                 nota=f'Cambio: regresó dañado{extra}')

        if item.cantidad > 1:
            # Venta de varios pares iguales (bot): solo sale uno del renglón.
            item.cantidad -= 1
            item.save(update_fields=['cantidad'])
            item = PedidoItem.objects.create(
                pedido=pedido, sku_snapshot=nuevo.codigo, nombre_snapshot=_nombre(nuevo),
                cantidad=1, costo_unitario=nuevo.costo, precio_unitario=precio_final)
        else:
            item.sku_snapshot = nuevo.codigo
            item.nombre_snapshot = _nombre(nuevo)
            item.costo_unitario = nuevo.costo
            item.precio_unitario = precio_final
            item.save(update_fields=[
                'sku_snapshot', 'nombre_snapshot', 'costo_unitario', 'precio_unitario'])
        nuevo.transicionar('vendido', usuario=usuario, origen='escaner', pedido_item=item,
                           nota=f'Cambio: se llevó en lugar de {regresa.codigo}{extra}')

        items = list(pedido.items.all())
        pedido.precio_venta = sum((i.cantidad * i.precio_unitario for i in items), Decimal('0'))
        pedido.costo_producto = sum((i.cantidad * i.costo_unitario for i in items), Decimal('0'))
        pedido.descripcion = _descripcion(
            len(items), [f'{i.nombre_snapshot} ×{i.cantidad}' for i in items])
        pedido.save(update_fields=['precio_venta', 'costo_producto', 'descripcion'])
        if diferencia > 0:
            Pago.objects.create(
                pedido=pedido, fecha=timezone.localdate(), monto=diferencia,
                metodo_pago=metodo_pago,
                notas=f'Cambio: {regresa.codigo} por {nuevo.codigo}'[:200])
    return ResultadoCambio(pedido=pedido, diferencia=diferencia)
