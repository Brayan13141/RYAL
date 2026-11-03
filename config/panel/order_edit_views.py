"""Endpoints JSON del panel para editar los renglones de un pedido web.

Son capas delgadas sobre `orders.edicion`: parsean la entrada, llaman al
servicio y devuelven los totales ya refrescados.
"""
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from catalog.models import Product, ProductImage, ProductVariant
from orders import edicion
from orders.edicion import EdicionRechazada
from orders.models import Order, OrderItem
from panel.views import _staff

_PRECIO_MAX = Decimal('999999.99')
_QTY_MAX = 99999


class _Entrada(ValueError):
    """Entrada mal formada; el mensaje se muestra tal cual."""


def _error(mensaje):
    return JsonResponse({'ok': False, 'error': mensaje}, status=400)


def _parse_qty(valor):
    try:
        qty = int(str(valor).strip())
    except (TypeError, ValueError):
        raise _Entrada('Cantidad inválida.')
    if qty > _QTY_MAX:
        raise _Entrada('Cantidad inválida.')
    return qty


def _parse_precio(valor):
    valor = (valor or '').strip()
    if not valor:
        return None
    try:
        precio = Decimal(valor)
    except InvalidOperation:
        raise _Entrada('Precio inválido.')
    if not precio.is_finite() or precio.as_tuple().exponent < -2 or precio > _PRECIO_MAX:
        raise _Entrada('Precio inválido.')
    return precio


def _totales(order):
    order.refresh_from_db()
    return JsonResponse({
        'ok': True,
        'total': float(order.total),
        'balance_due': float(order.balance_due),
        'is_paid': order.is_paid,
    })


def _thumb(imagen):
    return imagen.image.url if imagen else None


@_staff
@require_GET
def order_product_search(request, pk):
    get_object_or_404(Order, pk=pk)
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)
    productos = (Product.objects
                 .filter(is_active=True, status='available')
                 .filter(Q(name__icontains=q) | Q(sku__icontains=q))
                 .select_related('category__parent')
                 .order_by('name')[:20])
    return JsonResponse([{
        'id': p.pk,
        'name': p.name,
        'sku': p.sku,
        'price': float(p.final_price),
        'thumb': _thumb(p.cover_image),
    } for p in productos], safe=False)


@_staff
@require_GET
def order_product_options(request, pk, product_pk):
    order = get_object_or_404(Order, pk=pk)
    product = get_object_or_404(
        Product.objects.select_related('category__parent'),
        pk=product_pk, is_active=True, status='available')

    grupo = product.effective_size_group
    sizes = list(dict.fromkeys(grupo.sizes)) if grupo else []

    imagenes = list(product.images.all())
    colorways = []
    if product.effective_has_colorway and len(imagenes) > 1:
        colorways = [{'pk': i.pk, 'label': i.color_label, 'thumb': _thumb(i)} for i in imagenes]

    variantes = list(product.variants.filter(is_active=True))
    min_qty = product.effective_min_qty

    get = request.GET
    try:
        qty = int(get.get('qty', ''))
        if qty < 1:
            qty = min_qty
    except ValueError:
        qty = min_qty
    variante = None
    variant_id = get.get('variant_id', '').strip()
    if variant_id.isdigit():
        variante = next((v for v in variantes if v.pk == int(variant_id)), None)
    imagen = None
    image_pk = get.get('image_pk', '').strip()
    if image_pk.isdigit():
        imagen = next((i for i in imagenes if i.pk == int(image_pk)), None)

    # Si ya hay un renglón igual, agregar suma a él: el precio sugerido es el de
    # la cantidad combinada, y el servicio lo aplica a todo el renglón. Con
    # item_pk lo pide una fila que cambia su propia cantidad: no se combina.
    existente = None
    if not get.get('item_pk', '').strip():
        existente = edicion.renglon_existente(order, product, variante, edicion.variante_texto(
            product, variante, get.get('size_name', '').strip() or None, imagen,
            get.get('color', '').strip() or None))
    qty_precio = qty + existente.quantity if existente else qty

    try:
        costo = float(product.effective_base_price + product.effective_shipping)
    except Exception:
        costo = None

    return JsonResponse({
        'sizes': sizes,
        'colors': product.variant_colors or [],
        'colorways': colorways,
        'variants': [{'id': v.pk, 'name': v.name, 'price': float(v.final_price)} for v in variantes],
        'min_qty': min_qty,
        'sugerido': float(edicion.precio_sugerido(product, qty_precio, variante)),
        'costo': costo,
        'existente': {
            'item_pk': existente.pk,
            'quantity': existente.quantity,
            'price': float(existente.price_snapshot),
        } if existente else None,
    })


@_staff
@require_POST
def order_item_add(request, pk):
    order = get_object_or_404(Order, pk=pk)
    post = request.POST
    try:
        qty = _parse_qty(post.get('qty'))
        precio = _parse_precio(post.get('precio'))
    except _Entrada as e:
        return _error(str(e))

    product_id = post.get('product_id', '').strip()
    if not product_id.isdigit():
        return _error('Producto inválido.')
    product = get_object_or_404(Product, pk=int(product_id))

    variante = None
    variant_id = post.get('variant_id', '').strip()
    if variant_id:
        variante = ProductVariant.objects.filter(
            pk=int(variant_id) if variant_id.isdigit() else 0, product=product, is_active=True).first()
        if variante is None:
            return _error('Esa variante no está disponible.')

    imagen = None
    image_pk = post.get('image_pk', '').strip()
    if image_pk:
        imagen = ProductImage.objects.filter(
            pk=int(image_pk) if image_pk.isdigit() else 0, product=product).first()
        if imagen is None:
            return _error('Ese color no es de este producto.')

    try:
        edicion.agregar_renglon(
            order, request.user, product, qty, precio=precio, variant=variante,
            size_name=post.get('size_name', '').strip() or None,
            image=imagen, color=post.get('color', '').strip() or None)
    except EdicionRechazada as e:
        return _error(str(e))
    return _totales(order)


@_staff
@require_POST
def order_item_qty(request, pk, item_pk):
    order = get_object_or_404(Order, pk=pk)
    item = get_object_or_404(OrderItem, pk=item_pk, order_id=pk)
    try:
        qty = _parse_qty(request.POST.get('qty'))
        precio = _parse_precio(request.POST.get('precio'))
    except _Entrada as e:
        return _error(str(e))
    try:
        edicion.cambiar_cantidad(order, request.user, item, qty, precio=precio)
    except EdicionRechazada as e:
        return _error(str(e))
    return _totales(order)


@_staff
@require_POST
def order_item_remove(request, pk, item_pk):
    order = get_object_or_404(Order, pk=pk)
    item = get_object_or_404(OrderItem, pk=item_pk, order_id=pk)
    try:
        edicion.quitar_renglon(order, request.user, item)
    except EdicionRechazada as e:
        return _error(str(e))
    return _totales(order)
