from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django_ratelimit.decorators import ratelimit

from catalog.models import Product, ProductVariant, SiteConfig
from orders.models import Order, OrderItem
from orders.views import _get_cart, _save_cart, _create_order_safe
from api.serializers import OrderSerializer


def _build_whatsapp_url(order, request):
    config = SiteConfig.get()
    phone = config.whatsapp
    track_url = request.build_absolute_uri(f'/rastrear/?codigo={order.order_code}')
    items_text = '\n'.join(
        f'  • {item.quantity}x {item.name_snapshot} - ${item.subtotal:.0f}'
        for item in order.items.all()
    )
    message = (
        f'Hola, hice un pedido en WEB RYAL.\n'
        f'Código: {order.order_code}\n'
        f'Productos:\n{items_text}\n'
        f'Total: ${order.total:.0f} MXN\n'
        f'Rastrear: {track_url}'
    )
    import urllib.parse
    return f'https://wa.me/{phone}?text={urllib.parse.quote(message)}'


@api_view(['POST'])
@permission_classes([AllowAny])
@ratelimit(key='ip', rate='5/m', method='POST', block=False)
def checkout(request):
    if getattr(request, 'limited', False):
        return Response({'detail': 'Demasiados intentos.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    cart = _get_cart(request)
    if not cart:
        return Response({'detail': 'El carrito está vacío.'}, status=status.HTTP_400_BAD_REQUEST)

    customer_name = request.data.get('customer_name', '').strip()
    customer_phone = request.data.get('customer_phone', '').strip()

    if not customer_name or not customer_phone:
        return Response({'detail': 'Nombre y teléfono requeridos.'}, status=status.HTTP_400_BAD_REQUEST)
    if len(customer_phone) != 10 or not customer_phone.isdigit():
        return Response({'detail': 'Teléfono debe tener 10 dígitos.'}, status=status.HTTP_400_BAD_REQUEST)

    # Anti-duplicate: same phone, pending order in last 10 min
    cutoff = timezone.now() - timedelta(minutes=10)
    existing = Order.objects.filter(
        customer_phone=customer_phone, status='pending', created_at__gte=cutoff
    ).first()
    if existing:
        return Response({
            'token': str(existing.tracking_token),
            'order_code': existing.order_code,
            'whatsapp_url': _build_whatsapp_url(existing, request),
            'duplicate': True,
        })

    order = _create_order_safe(
        user=request.user if request.user.is_authenticated else None,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_email=request.user.email if request.user.is_authenticated else '',
    )

    for key, item in cart.items():
        try:
            product = Product.objects.select_related('category').get(pk=item['product_id'])
        except Product.DoesNotExist:
            continue
        variant = None
        if item.get('variant_id'):
            try:
                variant = ProductVariant.objects.get(pk=item['variant_id'])
            except ProductVariant.DoesNotExist:
                pass
        OrderItem.objects.create(
            order=order,
            product=product,
            variant=variant,
            quantity=item['quantity'],
            price_snapshot=item['price'],
            sku_snapshot=product.sku,
            name_snapshot=product.name,
            variant_snapshot=variant.name if variant else '',
        )

    _save_cart(request, {})

    return Response({
        'token': str(order.tracking_token),
        'order_code': order.order_code,
        'whatsapp_url': _build_whatsapp_url(order, request),
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([AllowAny])
def order_detail(request, token):
    try:
        order = Order.objects.prefetch_related('items').get(tracking_token=token)
    except Order.DoesNotExist:
        return Response({'detail': 'Pedido no encontrado.'}, status=status.HTTP_404_NOT_FOUND)
    return Response(OrderSerializer(order, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([AllowAny])
@ratelimit(key='ip', rate='20/m', method='GET', block=False)
def order_track(request):
    if getattr(request, 'limited', False):
        return Response({'detail': 'Demasiadas solicitudes.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    code = request.query_params.get('code', '').strip().upper()
    if not code:
        return Response({'detail': 'Código requerido.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        order = Order.objects.prefetch_related('items').get(order_code=code)
    except Order.DoesNotExist:
        return Response({'detail': 'Pedido no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

    config = SiteConfig.get()
    data = OrderSerializer(order, context={'request': request}).data
    data['track_message'] = config.track_message
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_orders(request):
    orders = (
        Order.objects
        .filter(user=request.user)
        .prefetch_related('items')
        .order_by('-created_at')
    )
    return Response(OrderSerializer(orders, many=True, context={'request': request}).data)
