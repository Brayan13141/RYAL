from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django_ratelimit.decorators import ratelimit
from django.shortcuts import get_object_or_404

from catalog.models import Product, ProductVariant
from orders.models import SavedCartItem
from orders.views import _get_cart, _save_cart, _cart_key, _get_category_violations


@api_view(['GET'])
@permission_classes([AllowAny])
def cart_get(request):
    cart = _get_cart(request)
    items = []
    for key, item in cart.items():
        try:
            product = Product.objects.select_related('category').prefetch_related('images').get(pk=item['product_id'])
            cover = product.cover_image
            image_url = None
            if cover and cover.image:
                image_url = request.build_absolute_uri(cover.image.url)
            items.append({
                'key': key,
                'product_id': item['product_id'],
                'variant_id': item.get('variant_id'),
                'name': product.name,
                'sku': product.sku,
                'image': image_url,
                'variant': item.get('variant_name', ''),
                'qty': item['quantity'],
                'price': float(item['price']),
                'subtotal': float(item['price']) * item['quantity'],
            })
        except Product.DoesNotExist:
            continue

    subtotal = sum(i['subtotal'] for i in items)
    return Response({
        'items': items,
        'subtotal': subtotal,
        'total': subtotal,
        'count': sum(i['qty'] for i in items),
        'category_warnings': _get_category_violations(cart),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@ratelimit(key='ip', rate='60/m', method='POST', block=False)
def cart_add(request):
    if getattr(request, 'limited', False):
        return Response({'detail': 'Demasiadas solicitudes.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    product_id = request.data.get('product_id')
    variant_id = request.data.get('variant_id')
    qty = int(request.data.get('qty', 1))

    if not product_id:
        return Response({'detail': 'product_id requerido.'}, status=status.HTTP_400_BAD_REQUEST)

    product = get_object_or_404(Product, pk=product_id, is_active=True, status='available')
    variant = None
    price = float(product.final_price)

    if variant_id:
        variant = get_object_or_404(ProductVariant, pk=variant_id, product=product, is_active=True)
        price = float(variant.final_price)

    min_qty = product.effective_min_qty
    if qty < min_qty:
        return Response({'detail': f'Mínimo {min_qty} piezas para {product.name}.'}, status=status.HTTP_400_BAD_REQUEST)

    cart = _get_cart(request)
    key = _cart_key(product_id, variant_id)

    if key in cart:
        cart[key]['quantity'] += qty
    else:
        cart[key] = {
            'product_id': int(product_id),
            'variant_id': variant_id,
            'variant_name': variant.name if variant else '',
            'quantity': qty,
            'price': price,
        }

    _save_cart(request, cart)

    if request.user.is_authenticated:
        SavedCartItem.objects.update_or_create(
            user=request.user, cart_key=key,
            defaults={
                'product_id': int(product_id),
                'variant_id': variant_id,
                'variant_name': variant.name if variant else '',
                'quantity': cart[key]['quantity'],
            }
        )

    return Response({
        'message': f'{product.name} agregado al carrito',
        'count': sum(i['quantity'] for i in cart.values()),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def cart_remove(request):
    key = request.data.get('key')
    if not key:
        return Response({'detail': 'key requerido.'}, status=status.HTTP_400_BAD_REQUEST)
    cart = _get_cart(request)
    cart.pop(key, None)
    _save_cart(request, cart)
    if request.user.is_authenticated:
        SavedCartItem.objects.filter(user=request.user, cart_key=key).delete()
    return Response({'count': sum(i['quantity'] for i in cart.values())})


@api_view(['POST'])
@permission_classes([AllowAny])
def cart_update(request):
    key = request.data.get('key')
    qty = request.data.get('qty')
    if not key or qty is None:
        return Response({'detail': 'key y qty requeridos.'}, status=status.HTTP_400_BAD_REQUEST)

    qty = int(qty)
    cart = _get_cart(request)
    if key not in cart:
        return Response({'detail': 'Ítem no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

    if qty <= 0:
        cart.pop(key)
    else:
        cart[key]['quantity'] = qty

    _save_cart(request, cart)
    return Response({'count': sum(i['quantity'] for i in cart.values())})
