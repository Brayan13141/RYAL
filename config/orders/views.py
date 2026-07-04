import json
import re
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from catalog.models import Product, ProductVariant, ProductImage
from .models import Order, OrderItem, SavedCartItem


def _client_ip(group, request):
    """IP real del cliente. django-ratelimit 4.x pasa (group, request).
    X-Real-IP (seteado por Nginx desde $remote_addr) no es spoofeable.
    X-Forwarded-For puede ser falsificado — NO usar el primer elemento."""
    real_ip = request.META.get('HTTP_X_REAL_IP', '').strip()
    if real_ip:
        return real_ip
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        # Tomar el último IP (agregado por el proxy de confianza, no por el cliente)
        return xff.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


# ─── Helpers de carrito en sesión ───────────────────────────────────────────

def _get_cart(request):
    return request.session.get('cart', {})

def _save_cart(request, cart):
    request.session['cart'] = cart
    request.session.modified = True

def _cart_key(product_id, variant_id, size_name=None, image_pk=None, color=None):
    # color (variant_colors, texto) es ortogonal al colorway por imagen (image_pk)
    if size_name and color:
        return f'{product_id}_size_{size_name}_color_{color}'
    if size_name and image_pk:
        # Talla + colorway: cada combinación (color-imagen, talla) es un ítem distinto
        return f'{product_id}_img_{image_pk}_size_{size_name}'
    if color:
        return f'{product_id}_color_{color}'
    if size_name:
        return f'{product_id}_size_{size_name}'
    if image_pk:
        return f'{product_id}_img_{image_pk}'
    return f'{product_id}_{variant_id or "none"}'


def _get_category_violations(cart):
    """
    Returns violations in two modes:
    - Category total:  root.min_order_qty > 1  → total pieces from that root category < minimum
    - Per-item calzado: root.min_qty_per_item > 0
        • _size_ keys: validated on the SUM of all sizes for the same product
          (e.g. Balenciaga 3: Talla24=5 + Talla25=7 = 12 ✓ — no violation)
        • _img_ keys / normal: validated individually (each colorway = separate 12-unit requirement)
    """
    if not cart:
        return []

    product_ids = [item['product_id'] for item in cart.values()]
    products_by_id = {
        p.pk: p
        for p in Product.objects.select_related('category__parent').filter(pk__in=product_ids)
    }

    violations = []
    cat_totals = {}
    # Clave: (product_id, image_pk) — image_pk=None para ítems sin colorway
    # Permite validar mínimo por color de forma independiente en modo tallas+colorway
    product_size_totals = {}

    for key, item in cart.items():
        product = products_by_id.get(item['product_id'])
        if not product:
            continue
        cat = product.category
        root = cat.parent if cat.parent_id else cat

        # Per-item check (e.g. calzado: 12 por modelo/color)
        if root.min_qty_per_item > 0:
            if '_size_' in key:
                # Acumular por (producto, color) — el mínimo se valida sobre el total de tallas
                # del mismo color. Si no hay image_pk, image_pk=None agrupa por producto solo.
                pid     = item['product_id']
                img_pk  = item.get('image_pk')      # None para ítems talla pura
                group   = (pid, img_pk)
                if group not in product_size_totals:
                    product_size_totals[group] = {
                        'name':     product.name,
                        'image_pk': img_pk,
                        'qty':      0,
                        'min':      root.min_qty_per_item,
                    }
                product_size_totals[group]['qty'] += item['quantity']
            else:
                # Color items (_img_ puro) o ítems regulares: verificar individualmente
                if item['quantity'] < root.min_qty_per_item:
                    violations.append({
                        'name':    product.name,
                        'current': item['quantity'],
                        'min':     root.min_qty_per_item,
                        'missing': root.min_qty_per_item - item['quantity'],
                    })

        # Category-total check (e.g. gorras: 20 en total)
        if root.min_order_qty > 1:
            if root.pk not in cat_totals:
                cat_totals[root.pk] = {'name': root.name, 'qty': 0, 'min': root.min_order_qty}
            cat_totals[root.pk]['qty'] += item['quantity']

    # Batch-load images para mostrar nombre de color en las violaciones
    color_img_pks = [v['image_pk'] for v in product_size_totals.values() if v.get('image_pk')]
    color_imgs_by_pk = (
        {img.pk: img for img in ProductImage.objects.filter(pk__in=color_img_pks)}
        if color_img_pks else {}
    )

    # Validar totales de tallas por modelo/color
    for v in product_size_totals.values():
        if v['qty'] < v['min']:
            display_name = v['name']
            if v.get('image_pk'):
                img = color_imgs_by_pk.get(v['image_pk'])
                color_str = img.color_label if (img and img.color_label) else f"variante"
                display_name = f"{v['name']} · {color_str}"
            violations.append({
                'name':    display_name,
                'current': v['qty'],
                'min':     v['min'],
                'missing': v['min'] - v['qty'],
            })

    violations += [
        {
            'name':    v['name'],
            'current': v['qty'],
            'min':     v['min'],
            'missing': v['min'] - v['qty'],
        }
        for v in cat_totals.values()
        if v['qty'] < v['min']
    ]

    return violations


def _generate_order_code():
    date_str = timezone.now().strftime('%y%m%d')
    today_count = Order.objects.filter(created_at__date=timezone.now().date()).count() + 1
    return f'RY{date_str}{today_count:04d}'


def _create_order_safe(**kwargs):
    """Create an Order with a unique code, retrying on collision (race condition guard)."""
    for _ in range(5):
        code = _generate_order_code()
        try:
            with transaction.atomic():
                return Order.objects.create(order_code=code, **kwargs)
        except IntegrityError:
            pass
    # Absolute fallback — statistically unreachable under normal load
    fallback = f'RY{timezone.now().strftime("%y%m%d")}{uuid.uuid4().hex[:5].upper()}'
    return Order.objects.create(order_code=fallback, **kwargs)


# ─── AJAX: obtener carrito ───────────────────────────────────────────────────

def cart_get(request):
    cart  = _get_cart(request)

    # Batch-load images para ítems con colorway específico (evita N+1)
    color_pks = [item['image_pk'] for item in cart.values() if item.get('image_pk')]
    images_by_pk = (
        {img.pk: img for img in ProductImage.objects.filter(pk__in=color_pks)}
        if color_pks else {}
    )

    items = []
    for key, item in cart.items():
        try:
            product = Product.objects.select_related('category__parent').prefetch_related('images').get(pk=item['product_id'])
            cover          = product.cover_image
            original_price = float(product.final_price)
            # Fallback for session items created before the 'price' field was added
            price          = float(item.get('price', original_price))
            discount       = round(original_price - price, 2) if original_price - price > 0.01 else 0
            root           = product.category.parent if product.category.parent_id else product.category
            qty_step       = int(root.min_qty_per_item) if root.min_qty_per_item > 0 else 1

            # Imagen específica del colorway si existe, sino la portada
            img_pk = item.get('image_pk')
            if img_pk and img_pk in images_by_pk:
                thumb_url = images_by_pk[img_pk].image.url
            else:
                thumb_url = cover.image.url if cover else None

            items.append({
                'key':            key,
                'name':           product.name,
                'sku':            product.sku,
                'image':          thumb_url,
                'variant':        item.get('variant_name', ''),
                'qty':            item['quantity'],
                'qty_step':       qty_step,
                'price':          price,
                'original_price': original_price,
                'discount':       discount,
                'subtotal':       price * item['quantity'],
            })
        except Product.DoesNotExist:
            continue

    subtotal = sum(i['subtotal'] for i in items)
    total    = subtotal
    category_warnings = _get_category_violations(cart)

    return JsonResponse({
        'ok':                True,
        'items':             items,
        'subtotal':          subtotal,
        'shipping':          0,
        'total':             total,
        'category_warnings': category_warnings,
    })


# ─── AJAX: agregar al carrito ────────────────────────────────────────────────

@ratelimit(key=_client_ip, rate='60/m', method='POST', block=False)
@require_POST
def cart_add(request):
    if getattr(request, 'limited', False):
        return JsonResponse({'ok': False, 'error': 'Demasiadas solicitudes. Espera un momento.'}, status=429)
    try:
        body       = json.loads(request.body)
        product_id = int(body['product_id'])
        variant_id = body.get('variant_id')
        size_name  = body.get('size_name') or None
        image_pk   = body.get('image_pk') or None   # colorway específico
        color_num  = body.get('color_num') or None   # número 1-based visible al usuario
        color      = (body.get('color') or '').strip() or None   # variant_colors (texto)
        if image_pk:
            image_pk = int(image_pk)
        qty        = int(body.get('qty', 1))
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)

    product = get_object_or_404(
        Product.objects.select_related(
            'category__parent',
            'category__size_group',
            'category__parent__size_group',
            'size_group',
        ),
        pk=product_id, is_active=True, status='available',
    )
    variant = None
    price   = float(product.final_price)

    if variant_id:
        variant = get_object_or_404(ProductVariant, pk=variant_id, product=product, is_active=True)
        price   = float(variant.final_price)

    # Validar image_pk cuando se envía (modo colorway)
    img_obj = None
    if image_pk:
        try:
            img_obj = ProductImage.objects.get(pk=image_pk, product=product)
        except ProductImage.DoesNotExist:
            image_pk = None  # inválido — caer en flujo normal

    cart = _get_cart(request)
    key  = _cart_key(product_id, variant_id, size_name, image_pk, color)

    # Productos con grupo de tallas (cascade: producto > subcategoría > padre)
    # requieren selección explícita de talla antes de agregar al carrito.
    # image_pk no exime de enviar size_name — un ítem sin talla no puede procesarse.
    if product.effective_size_group and not size_name:
        return JsonResponse(
            {'ok': False, 'error': 'Selecciona las tallas antes de agregar al carrito'},
            status=400,
        )

    # Productos con colores seleccionables requieren color explícito (espejo del de tallas).
    if product.variant_colors and not color:
        return JsonResponse(
            {'ok': False, 'error': 'Selecciona un color antes de agregar al carrito'},
            status=400,
        )

    # Items de talla/colorway: el frontend ya validó el total.
    # Items normales: primer add debe cumplir el mínimo por modelo.
    if not size_name and not image_pk:
        min_qty = product.effective_min_qty
        if key not in cart and qty < min_qty:
            return JsonResponse(
                {'ok': False, 'error': f'Mínimo {min_qty} piezas para {product.name}'},
                status=400,
            )

    # Aplicar tier de volumen basado en cantidad total (existente + nueva)
    total_qty = cart.get(key, {}).get('quantity', 0) + qty
    root = product.category.parent if product.category.parent_id else product.category
    tier = root.volume_tiers.filter(min_qty__lte=total_qty).order_by('-min_qty').first()
    if tier:
        price = max(0.0, float(product.final_price) - float(tier.discount_amount))

    # Nombre descriptivo del ítem en el carrito
    if size_name and color:
        variant_name = f'Talla {size_name} / {color}'
    elif size_name and img_obj:
        # Modo combinado: tallas + colorway por imagen → "Blanco · Talla 26"
        color_part = img_obj.color_label or (f'Color {color_num}' if color_num else f'Color {img_obj.display_order + 1}')
        variant_name = f'{color_part} · Talla {size_name}'
    elif color:
        variant_name = color
    elif size_name:
        variant_name = f'Talla {size_name}'
    elif img_obj:
        # Modo colorway puro: solo imagen
        if img_obj.color_label:
            variant_name = img_obj.color_label
        elif color_num:
            variant_name = f'Color {color_num}'
        else:
            variant_name = f'Color {img_obj.display_order + 1}'
    elif variant:
        variant_name = variant.name
    else:
        variant_name = ''

    if key in cart:
        cart[key]['quantity'] += qty
        cart[key]['price']     = price
    else:
        cart[key] = {
            'product_id':   product_id,
            'variant_id':   variant_id,
            'image_pk':     image_pk,    # colorway: pk de la imagen seleccionada
            'variant_name': variant_name,
            'quantity':     qty,
            'price':        price,
        }

    _save_cart(request, cart)

    if request.user.is_authenticated:
        SavedCartItem.objects.update_or_create(
            user=request.user, cart_key=key,
            defaults={
                'product_id':   product_id,
                'variant_id':   variant_id,
                'variant_name': variant_name,
                'quantity':     cart[key]['quantity'],
            }
        )

    cart_count = sum(i['quantity'] for i in cart.values())
    return JsonResponse({
        'ok':         True,
        'message':    f'{product.name} agregado al carrito',
        'cart_count': cart_count,
    })


# ─── AJAX: remover del carrito ───────────────────────────────────────────────

@require_POST
def cart_remove(request):
    try:
        key = json.loads(request.body)['key']
    except (KeyError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)

    cart = _get_cart(request)
    cart.pop(key, None)
    _save_cart(request, cart)

    if request.user.is_authenticated:
        SavedCartItem.objects.filter(user=request.user, cart_key=key).delete()

    cart_count = sum(i['quantity'] for i in cart.values())
    return JsonResponse({'ok': True, 'cart_count': cart_count})


# ─── AJAX: actualizar cantidad ───────────────────────────────────────────────

@require_POST
def cart_update(request):
    try:
        body = json.loads(request.body)
        key  = body['key']
        qty  = int(body['qty'])
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)

    cart = _get_cart(request)
    if key in cart:
        if qty < 1:
            cart.pop(key)
            if request.user.is_authenticated:
                SavedCartItem.objects.filter(user=request.user, cart_key=key).delete()
        else:
            cart[key]['quantity'] = qty
            # Recalcular precio con tier basado en nueva cantidad
            try:
                product = Product.objects.select_related('category__parent').get(pk=cart[key]['product_id'])
                root = product.category.parent if product.category.parent_id else product.category
                tier = root.volume_tiers.filter(min_qty__lte=qty).order_by('-min_qty').first()
                cart[key]['price'] = (
                    max(0.0, float(product.final_price) - float(tier.discount_amount))
                    if tier else float(product.final_price)
                )
            except Product.DoesNotExist:
                pass
            if request.user.is_authenticated:
                SavedCartItem.objects.filter(user=request.user, cart_key=key).update(quantity=qty)
        _save_cart(request, cart)

    cart_count = sum(i['quantity'] for i in cart.values())
    return JsonResponse({'ok': True, 'cart_count': cart_count})


# ─── Checkout ────────────────────────────────────────────────────────────────

def _build_cart_items(cart):
    items = []
    for key, item in cart.items():
        try:
            product = Product.objects.select_related('category').prefetch_related('images').get(pk=item['product_id'])
            cover   = product.cover_image

            class _Item:
                pass

            price_snapshot    = float(item['price'])
            original_price    = float(product.final_price)
            discount_per_unit = max(0.0, round(original_price - price_snapshot, 2))

            i                  = _Item()
            i.key              = key
            i.cover_image      = cover
            i.name_snapshot    = product.name
            i.variant_snapshot = item.get('variant_name', '')
            if product.category_id:
                cat = product.category
                root = cat.parent if cat.parent_id else cat
                i.category_name    = root.name
                i.root_category_id = root.pk
            else:
                i.category_name    = ''
                i.root_category_id = None
            i.quantity         = item['quantity']
            i.price_snapshot   = price_snapshot
            i.original_price   = original_price
            i.discount_per_unit = discount_per_unit
            i.discount_total    = round(discount_per_unit * item['quantity'], 2)
            i.subtotal          = price_snapshot * item['quantity']
            i.original_subtotal = round(original_price * item['quantity'], 2)
            items.append(i)
        except Product.DoesNotExist:
            continue
    return items


def checkout(request):
    cart  = _get_cart(request)
    items = _build_cart_items(cart)
    subtotal       = sum(i.subtotal for i in items)
    total_savings  = sum(i.discount_total for i in items)
    category_warnings = request.session.pop('checkout_warnings', [])

    profile = None
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
        except Exception:
            pass

    return render(request, 'orders/checkout.html', {
        'cart_items':         items,
        'subtotal':           subtotal,
        'total':              subtotal,
        'total_savings':      total_savings,
        'category_warnings':  category_warnings,
        'profile':            profile,
    })


@ratelimit(key=_client_ip, rate='5/m', method='POST', block=False)
@require_POST
def checkout_confirm(request):
    if getattr(request, 'limited', False):
        messages.error(request, 'Demasiados intentos. Espera un momento e intenta de nuevo.')
        return redirect('orders:checkout')
    cart = _get_cart(request)
    if not cart:
        return redirect('catalog:home')

    nombre   = request.POST.get('nombre', '').strip()
    telefono = request.POST.get('telefono', '').strip()
    codigo_descuento_str = request.POST.get('codigo_descuento', '').strip().upper()
    descuento_monto_str  = request.POST.get('descuento_monto', '0')

    if not nombre or not telefono:
        return redirect('orders:checkout')

    # Server-side phone validation — 10 digits only (matches client-side pattern)
    if not re.fullmatch(r'\d{10}', telefono):
        messages.error(request, 'El número de teléfono debe tener exactamente 10 dígitos.')
        return redirect('orders:checkout')

    violations = _get_category_violations(cart)
    if violations:
        request.session['checkout_warnings'] = violations
        return redirect('orders:checkout')

    # Evitar pedido duplicado: mismo teléfono en los últimos 10 minutos
    cutoff = timezone.now() - timedelta(minutes=10)
    duplicate = Order.objects.filter(
        customer_phone=telefono,
        status='pending',
        created_at__gte=cutoff,
    ).first()
    if duplicate:
        messages.warning(
            request,
            f'Ya tienes un pedido reciente con ese número (código: {duplicate.order_code}). '
            f'Espera unos minutos o rastrea tu pedido.'
        )
        return redirect('orders:confirmation', token=duplicate.tracking_token)

    order = _create_order_safe(
        user           = request.user if request.user.is_authenticated else None,
        customer_name  = nombre,
        customer_phone = telefono,
        status         = 'pending',
    )

    for key, item in cart.items():
        try:
            product = Product.objects.select_related('category__parent').get(pk=item['product_id'])
            variant = None
            if item.get('variant_id'):
                variant = ProductVariant.objects.filter(pk=item['variant_id']).first()
            try:
                cost_snapshot = product.base_price + product.effective_shipping
            except Exception:
                cost_snapshot = None

            OrderItem.objects.create(
                order            = order,
                product          = product,
                variant          = variant,
                quantity         = item['quantity'],
                price_snapshot   = item['price'],
                cost_snapshot    = cost_snapshot,
                sku_snapshot     = product.sku,
                name_snapshot    = product.name,
                variant_snapshot = item.get('variant_name', ''),
            )
        except Product.DoesNotExist:
            continue

    if codigo_descuento_str:
        from decimal import Decimal
        from catalog.services import validar_codigo
        order_items_qs = order.items.select_related('product__category__parent').all()
        cart_items_for_validation = []
        for i in order_items_qs:
            root_category_id = None
            if i.product_id and i.product.category_id:
                cat = i.product.category
                root = cat.parent if cat.parent_id else cat
                root_category_id = root.pk
            cart_items_for_validation.append({
                'qty': i.quantity,
                'description': i.name_snapshot,
                'root_category_id': root_category_id,
            })
        resultado = validar_codigo(codigo_descuento_str, canal='web', items=cart_items_for_validation)
        if resultado['valido']:
            descuento_decimal = Decimal(str(resultado['descuento']))
            order.descuento_aplicado = descuento_decimal
            order.notes = f'Código de descuento: {codigo_descuento_str} (−${resultado["descuento"]:.0f} MXN)'
            order.save(update_fields=['notes', 'descuento_aplicado'])
            from django.db.models import F
            from catalog.models import CodigoDescuento
            CodigoDescuento.objects.filter(
                codigo__iexact=codigo_descuento_str
            ).update(usos_actuales=F('usos_actuales') + 1)

    _save_cart(request, {})
    if request.user.is_authenticated:
        SavedCartItem.objects.filter(user=request.user).delete()
    return redirect('orders:confirmation', token=order.tracking_token)


def order_confirmation(request, token):
    order = get_object_or_404(Order, tracking_token=token)
    return render(request, 'orders/confirmation.html', {'order': order})


@login_required
def my_orders(request):
    from django.db.models import Q
    q = Q(user=request.user)
    try:
        phone = request.user.profile.phone
        if phone:
            q |= Q(customer_phone=phone)
    except Exception:
        pass
    orders = (
        Order.objects
        .filter(q)
        .prefetch_related('items')
        .distinct()
        .order_by('-created_at')
    )
    return render(request, 'orders/my_orders.html', {'orders': orders})


@ratelimit(key=_client_ip, rate='20/m', method='GET', block=False)
def order_track(request):
    if getattr(request, 'limited', False):
        return render(request, 'orders/track.html', {
            'order': None,
            'error': 'Demasiados intentos. Espera un minuto antes de volver a buscar.',
            'codigo': '',
        })
    code  = request.GET.get('codigo', '').strip().upper()
    order = None
    error = None
    if code:
        try:
            order = Order.objects.prefetch_related('items__product', 'items__variant').get(order_code=code)
        except Order.DoesNotExist:
            error = f'No encontramos ningún pedido con el código "{code}".'
    return render(request, 'orders/track.html', {'order': order, 'error': error, 'codigo': code})
