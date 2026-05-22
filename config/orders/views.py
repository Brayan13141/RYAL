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
from catalog.models import Product, ProductVariant
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

def _cart_key(product_id, variant_id):
    return f'{product_id}_{variant_id or "none"}'


def _get_category_violations(cart):
    """
    Returns violations in two modes:
    - Category total: root.min_order_qty > 1 → total pieces from that root category < minimum
    - Per-item:       root.min_qty_per_item > 0 → individual product qty < minimum (e.g. calzado)
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

    for item in cart.values():
        product = products_by_id.get(item['product_id'])
        if not product:
            continue
        cat = product.category
        root = cat.parent if cat.parent_id else cat

        # Per-item check (e.g. calzado: 12 por modelo)
        if root.min_qty_per_item > 0 and item['quantity'] < root.min_qty_per_item:
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
    items = []
    for key, item in cart.items():
        try:
            product = Product.objects.select_related('category__parent').prefetch_related('images').get(pk=item['product_id'])
            cover          = product.cover_image
            price          = float(item['price'])
            original_price = float(product.final_price)
            discount       = round(original_price - price, 2) if original_price - price > 0.01 else 0
            root           = product.category.parent if product.category.parent_id else product.category
            qty_step       = int(root.min_qty_per_item) if root.min_qty_per_item > 0 else 1
            items.append({
                'key':            key,
                'name':           product.name,
                'sku':            product.sku,
                'image':          cover.image.url if cover else None,
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
        'ok': True,
        'items': items,
        'subtotal': subtotal,
        'total': total,
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
        qty        = int(body.get('qty', 1))
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)

    product = get_object_or_404(
        Product.objects.select_related('category__parent'),
        pk=product_id, is_active=True, status='available',
    )
    variant = None
    price   = float(product.final_price)

    if variant_id:
        variant = get_object_or_404(ProductVariant, pk=variant_id, product=product, is_active=True)
        price   = float(variant.final_price)

    cart = _get_cart(request)
    key  = _cart_key(product_id, variant_id)

    # Primer add: la cantidad inicial debe cumplir el mínimo por modelo.
    # Adds posteriores (ya hay qty en carrito) se validan via cart warnings.
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

    if key in cart:
        cart[key]['quantity'] += qty
        cart[key]['price']     = price
    else:
        cart[key] = {
            'product_id':   product_id,
            'variant_id':   variant_id,
            'variant_name': variant.name if variant else '',
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
                'variant_name': variant.name if variant else '',
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
            product = Product.objects.get(pk=item['product_id'])
            variant = None
            if item.get('variant_id'):
                variant = ProductVariant.objects.filter(pk=item['variant_id']).first()

            OrderItem.objects.create(
                order            = order,
                product          = product,
                variant          = variant,
                quantity         = item['quantity'],
                price_snapshot   = item['price'],
                sku_snapshot     = product.sku,
                name_snapshot    = product.name,
                variant_snapshot = item.get('variant_name', ''),
            )
        except Product.DoesNotExist:
            continue

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
