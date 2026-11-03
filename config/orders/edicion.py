"""Edición de renglones de un pedido web desde el panel: agregar, quitar y
cambiar cantidad.

Cada operación corre en una transacción con el `Order` bloqueado
(`select_for_update`), revalida estado y pedido al proveedor dentro del
bloqueo, y se rechaza con `EdicionRechazada` si deja un faltante de mínimos
nuevo o más grande que el que ya traía. La excepción se lanza dentro del
`atomic`, así que un rechazo no deja nada a medias.

No toca `Order.notes` (lo ve el cliente en el ticket) ni el `cost_snapshot` de
renglones existentes.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from .minimos import empeora, faltantes, lineas_de_pedido
from .models import Order, OrderItem, OrderItemChange
from .supplier_items import crear_supplier_item
from .variantes import nombre_variante
from .views import _price_with_volume_tier

ESTADOS_EDITABLES = ('pending', 'confirmed')

_CENTAVO = Decimal('0.01')


class EdicionRechazada(ValueError):
    """La edición no se aplicó; el mensaje es para el admin."""


def _supplier_order(order):
    try:
        return order.supplier_order
    except ObjectDoesNotExist:
        return None


def motivo_no_editable(order):
    """None si el pedido se puede editar; si no, el motivo para el admin."""
    if order.status not in ESTADOS_EDITABLES:
        return 'Solo se pueden editar pedidos Pendientes o Confirmados.'
    so = _supplier_order(order)
    if so is not None and so.ya_lanzado:
        return 'El pedido al proveedor ya se lanzó; ya no se pueden cambiar productos.'
    return None


def precio_sugerido(product, qty, variant=None):
    """Precio de hoy en la tienda para un renglón de `qty` piezas (tier por renglón)."""
    base = float(variant.final_price if variant else product.final_price)
    return Decimal(str(_price_with_volume_tier(product, qty, base))).quantize(_CENTAVO, ROUND_HALF_UP)


def variante_texto(product, variant=None, size_name=None, image=None, color=None):
    """Texto de la variante (`variant_snapshot`) tal como lo arma la tienda.

    El colorway sin etiqueta es "Color N" con N = posición (1-based) de la imagen
    en `product.images.all()`, que es lo que manda el front como `color_num`
    (static/js/product.js). display_order no sirve: las imágenes subidas desde
    el panel lo dejan en 0 y todos los colores saldrían "Color 1".
    """
    color_num = None
    if image is not None:
        posiciones = [i.pk for i in product.images.all()]
        if image.pk in posiciones:
            color_num = posiciones.index(image.pk) + 1
    return nombre_variante(size_name, color, image, color_num, variant)


def renglon_existente(order, product, variant, variant_snapshot):
    """El renglón del pedido al que se sumaría este producto+variante, o None."""
    return order.items.select_related('product__category__parent', 'variant').filter(
        product=product, variant=variant, variant_snapshot=variant_snapshot,
    ).first()


# ── helpers ──────────────────────────────────────────────────────────────

def _faltantes(order):
    return faltantes(lineas_de_pedido(order.items.select_related('product__category__parent')))


def _descripcion(item):
    texto = item.name_snapshot
    if item.variant_snapshot:
        texto += f' — {item.variant_snapshot}'
    return texto[:300]


def _validar_qty(qty):
    if qty < 1:
        raise EdicionRechazada('La cantidad debe ser al menos 1.')


def _validar_precio(precio):
    if precio is None:
        return None
    # A centavos: lo que se guarda, lo que se compara y lo que va a la bitácora es lo mismo.
    precio = Decimal(str(precio)).quantize(_CENTAVO, ROUND_HALF_UP)
    if precio < 0:
        raise EdicionRechazada('El precio no puede ser negativo.')
    return precio


def _renglon(order, item):
    try:
        return order.items.select_related('product__category__parent', 'variant').get(pk=item.pk)
    except OrderItem.DoesNotExist:
        raise EdicionRechazada('Ese renglón ya no está en el pedido.')


def _editar(order, usuario, aplicar):
    """Marco común: bloqueo, estado, mínimos antes/después, cupón, bitácora y saldo.

    `aplicar(order)` hace el cambio y regresa (resultado, datos de bitácora); con
    datos None no hubo cambio y no se registra nada.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        motivo = motivo_no_editable(order)
        if motivo:
            raise EdicionRechazada(motivo)

        antes = _faltantes(order)
        resultado, cambio = aplicar(order)
        if cambio is None:
            return resultado

        malos = empeora(antes, _faltantes(order))
        if malos:
            raise EdicionRechazada('No se puede: ' + '; '.join(
                f'faltan {f.missing} piezas de {f.nombre} (mínimo {f.minimo})' for f in malos))

        # Tope del cupón: el total nunca queda negativo (mismo tope que order_descuento_apply).
        subtotal = sum((i.subtotal for i in order.items.all()), Decimal('0'))
        if order.descuento_aplicado > subtotal:
            order.descuento_aplicado = subtotal
            order.save(update_fields=['descuento_aplicado', 'updated_at'])

        OrderItemChange.objects.create(order=order, usuario=usuario, **cambio)
        order.recalc_paid()
        return resultado


def _aplicar_cantidad(item, qty, precio):
    """Cambia cantidad y precio de un renglón ya bloqueado. cost_snapshot no se toca."""
    if precio is None:
        if item.product_id:
            precio = precio_sugerido(item.product, qty, item.variant)
        else:
            precio = item.price_snapshot
    if qty == item.quantity and precio == item.price_snapshot:
        return item, None
    cambio = {
        'accion': OrderItemChange.CANTIDAD,
        'descripcion': _descripcion(item),
        'cantidad_antes': item.quantity,
        'cantidad_despues': qty,
        'precio_antes': item.price_snapshot,
        'precio_despues': precio,
    }
    item.quantity = qty
    item.price_snapshot = precio
    item.save(update_fields=['quantity', 'price_snapshot'])
    return item, cambio


# ── API pública ──────────────────────────────────────────────────────────

def agregar_renglon(order, usuario, product, qty, precio=None, variant=None,
                    size_name=None, image=None, color=None):
    """Agrega un producto al pedido, o suma al renglón igual que ya exista."""
    def aplicar(order):
        _validar_qty(qty)
        p = _validar_precio(precio)
        # Disponibilidad antes de sumar a un renglón existente: lo que la tienda
        # ya no vende no entra al pedido por ningún camino.
        if not product.is_active or product.status != 'available':
            raise EdicionRechazada('Ese producto no está disponible en la tienda.')
        if variant is not None and (variant.product_id != product.pk or not variant.is_active):
            raise EdicionRechazada('Esa variante no está disponible.')
        if image is not None and image.product_id != product.pk:
            raise EdicionRechazada('Ese color no es de este producto.')
        grupo = product.effective_size_group
        if grupo and not size_name:
            raise EdicionRechazada('Selecciona la talla.')
        # El grupo puede guardar números; la talla llega como texto.
        if size_name and (not grupo or str(size_name) not in [str(t) for t in grupo.sizes]):
            raise EdicionRechazada('Esa talla no existe para este producto.')
        if product.variant_colors and not color:
            raise EdicionRechazada('Selecciona el color.')

        variant_snapshot = variante_texto(product, variant, size_name, image, color)

        existente = renglon_existente(order, product, variant, variant_snapshot)
        if existente is not None:
            # Mismo producto y variante: se suma al renglón, como el carrito.
            return _aplicar_cantidad(existente, existente.quantity + qty, p)

        # Producto regular (sin talla ni colorway): mínimo propio, como cart_add.
        if not size_name and image is None:
            minimo = product.effective_min_qty
            if qty < minimo:
                raise EdicionRechazada(f'Mínimo {minimo} piezas para {product.name}')

        if p is None:
            p = precio_sugerido(product, qty, variant)
        try:
            cost_snapshot = product.effective_base_price + product.effective_shipping
        except Exception:
            cost_snapshot = None

        item = OrderItem.objects.create(
            order=order,
            product=product,
            variant=variant,
            quantity=qty,
            price_snapshot=p,
            cost_snapshot=cost_snapshot,
            sku_snapshot=product.sku,
            name_snapshot=product.name,
            variant_snapshot=variant_snapshot,
        )
        so = _supplier_order(order)
        if so is not None:
            crear_supplier_item(so, item)

        return item, {
            'accion': OrderItemChange.AGREGAR,
            'descripcion': _descripcion(item),
            'cantidad_antes': None,
            'cantidad_despues': qty,
            'precio_antes': None,
            'precio_despues': p,
        }

    return _editar(order, usuario, aplicar)


def cambiar_cantidad(order, usuario, item, qty, precio=None):
    """Cambia la cantidad (y el precio) de un renglón. Sin precio, se sugiere el de hoy."""
    def aplicar(order):
        _validar_qty(qty)
        p = _validar_precio(precio)
        return _aplicar_cantidad(_renglon(order, item), qty, p)

    return _editar(order, usuario, aplicar)


def quitar_renglon(order, usuario, item):
    """Borra un renglón; el CASCADE se lleva su SupplierOrderItem."""
    def aplicar(order):
        renglon = _renglon(order, item)
        if order.items.count() == 1:
            raise EdicionRechazada('No se puede quitar el último producto; cancela el pedido.')
        cambio = {
            'accion': OrderItemChange.QUITAR,
            'descripcion': _descripcion(renglon),
            'cantidad_antes': renglon.quantity,
            'cantidad_despues': None,
            'precio_antes': renglon.price_snapshot,
            'precio_despues': None,
        }
        renglon.delete()
        return None, cambio

    _editar(order, usuario, aplicar)
