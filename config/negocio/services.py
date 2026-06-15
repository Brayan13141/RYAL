import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from catalog.models import Product
from .models import Pedido, PedidoItem, Pago


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
