import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from catalog.models import Product
from .models import Pedido, PedidoItem, Pago, Cliente


class VentaInvalida(Exception):
    """Payload de venta inválido (SKU inexistente/inactivo, cantidad o precio inválidos)."""


_METODOS_VALIDOS = {m[0] for m in Pago.METODO_CHOICES}


def _parse_cantidad(valor):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise VentaInvalida(f'Cantidad inválida: {valor!r}')
    if n <= 0:
        raise VentaInvalida(f'La cantidad debe ser > 0: {valor!r}')
    return n


def _parse_precio(valor):
    try:
        precio = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise VentaInvalida(f'Precio inválido: {valor!r}')
    if precio < 0:
        raise VentaInvalida(f'El precio no puede ser negativo: {valor!r}')
    return precio


MOSTRADOR_TELEFONO = 'TIENDA-MOSTRADOR'


@transaction.atomic
def crear_pedido_tienda_bot(*, items, envio=Decimal('0')):
    """Crea un pedido de tienda física via bot. Sin SKU, sin normalize_telefono.

    `items`: lista de {description, price, qty}
    """
    if not items:
        raise VentaInvalida('La venta no tiene ítems.')

    cliente, _ = Cliente.objects.get_or_create(
        telefono=MOSTRADOR_TELEFONO,
        defaults={'nombre': 'Mostrador'},
    )

    precio_total = sum(Decimal(str(i['price'])) * int(i['qty']) for i in items)
    partes_desc = []
    pedido = Pedido.objects.create(
        cliente=cliente,
        descripcion='',
        costo_producto=Decimal('0'),
        precio_venta=precio_total,
        envio=Decimal(str(envio)),
        estado=Pedido.PAGADO,
        origen=Pedido.TIENDA,
    )

    for item in items:
        nombre_snap = (str(item.get('description') or '').strip() or 'ítem tienda')[:200]
        qty = int(item['qty'])
        precio_u = Decimal(str(item['price']))
        PedidoItem.objects.create(
            pedido=pedido,
            product=None,
            sku_snapshot='TIENDA-BOT',
            nombre_snapshot=nombre_snap,
            cantidad=qty,
            costo_unitario=Decimal('0'),
            precio_unitario=precio_u,
        )
        partes_desc.append(f'{nombre_snap[:30]} ×{qty}')

    pedido.descripcion = f'{len(items)} art.: ' + ', '.join(partes_desc)
    pedido.save(update_fields=['descripcion'])
    return pedido


@transaction.atomic
def crear_venta_tienda(*, lineas, cliente=None, metodo_pago='efectivo'):
    """Crea una venta de tienda física: Pedido PAGADO + PedidoItem + Pago completo.

    `lineas`: lista de dicts {sku, cantidad, precio_unitario}. El precio es editable
    (override del cajero); el COSTO se toma siempre de Product.base_price en el servidor
    (nunca se confía al cliente) para preservar la integridad de la ganancia.
    Lanza VentaInvalida si algo no valida; la transacción atómica garantiza que no se
    cree nada parcial.
    """
    if not lineas:
        raise VentaInvalida('La venta no tiene líneas.')
    if metodo_pago not in _METODOS_VALIDOS:
        raise VentaInvalida(f'Método de pago inválido: {metodo_pago!r}')

    pedido = Pedido.objects.create(
        cliente=cliente,
        descripcion='',
        costo_producto=Decimal('0'),
        precio_venta=Decimal('0'),
        envio=Decimal('0'),
        estado=Pedido.PAGADO,
        origen=Pedido.TIENDA,
    )

    total_precio = Decimal('0')
    total_costo = Decimal('0')
    partes_desc = []

    for linea in lineas:
        sku = (linea.get('sku') or '').strip()
        if not sku:
            raise VentaInvalida('Línea sin SKU.')
        try:
            product = Product.objects.get(sku=sku, is_active=True)
        except Product.DoesNotExist:
            raise VentaInvalida(f'Producto no encontrado o inactivo: {sku}')

        cantidad = _parse_cantidad(linea.get('cantidad'))
        precio_unitario = _parse_precio(linea.get('precio_unitario'))
        costo_unitario = product.base_price  # SIEMPRE del servidor

        PedidoItem.objects.create(
            pedido=pedido, product=product,
            sku_snapshot=product.sku, nombre_snapshot=product.name,
            cantidad=cantidad,
            costo_unitario=costo_unitario, precio_unitario=precio_unitario,
        )
        total_precio += precio_unitario * cantidad
        total_costo += costo_unitario * cantidad
        partes_desc.append(f'{product.name} ×{cantidad}')

    pedido.precio_venta = total_precio
    pedido.costo_producto = total_costo
    pedido.descripcion = f'{len(lineas)} art.: ' + ', '.join(partes_desc)
    pedido.save(update_fields=['precio_venta', 'costo_producto', 'descripcion'])

    Pago.objects.create(
        pedido=pedido, fecha=datetime.date.today(),
        monto=total_precio, metodo_pago=metodo_pago,
    )
    return pedido


@transaction.atomic
def crear_pedido_bot(*, nombre, telefono, items, envio=Decimal('0')):
    """Crea un pedido vía bot WhatsApp: encuentra-o-crea el cliente, luego crea pedido+ítems."""
    from .phone import normalize_telefono
    if not items:
        raise VentaInvalida('La sesión no tiene ítems.')
    telefono_norm = normalize_telefono(telefono)
    cliente, _ = Cliente.objects.get_or_create(
        telefono=telefono_norm,
        defaults={'nombre': nombre},
    )
    envio_d = _parse_precio(envio)
    pedido = Pedido.objects.create(
        cliente=cliente,
        descripcion='',
        costo_producto=Decimal('0'),
        precio_venta=Decimal('0'),
        envio=envio_d,
        estado=Pedido.PAGADO,
        origen=Pedido.BOT,
    )
    total_precio = Decimal('0')
    partes_desc = []
    for item in items:
        precio = _parse_precio(item.get('price', 0))
        qty = _parse_cantidad(item.get('qty', 1))
        nombre_snap = str(item.get('description', ''))[:200]
        PedidoItem.objects.create(
            pedido=pedido,
            product=None,
            sku_snapshot='BOT',
            nombre_snapshot=nombre_snap,
            cantidad=qty,
            costo_unitario=Decimal('0'),
            precio_unitario=precio,
        )
        total_precio += precio * qty
        partes_desc.append(f'{nombre_snap[:30]} ×{qty}')
    pedido.precio_venta = total_precio
    pedido.descripcion = f'Bot: {", ".join(partes_desc)}'
    pedido.save(update_fields=['precio_venta', 'descripcion'])
    return pedido
