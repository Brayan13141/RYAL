import hmac
import json
from decimal import Decimal

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from .models import Cliente
from .phone import normalize_telefono
from .services import crear_pedido_bot, VentaInvalida


def _authorized(request):
    auth = request.META.get('HTTP_AUTHORIZATION', '')
    expected = f'Bearer {settings.NEGOCIO_API_KEY}'
    return hmac.compare_digest(auth, expected)


def _client_ip(group, request):
    """IP real del cliente compatible con Nginx Unix socket (REMOTE_ADDR vacío)."""
    real_ip = request.META.get('HTTP_X_REAL_IP', '').strip()
    if real_ip:
        return real_ip
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


@require_GET
@ratelimit(key=_client_ip, rate='30/m', block=True)
def api_cliente(request, telefono):
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        cliente = Cliente.objects.get(telefono=normalize_telefono(telefono))
        descuento = float(cliente.descuento)
    except Cliente.DoesNotExist:
        # Cliente no registrado = sin descuento (el bot aplica precio base)
        descuento = 0.0
    return JsonResponse({'descuento': descuento})


@require_GET
@ratelimit(key=_client_ip, rate='30/m', block=True)
def api_clientes_buscar(request):
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    q = (request.GET.get('q') or '').strip()
    if not q:
        return JsonResponse({'clientes': []})
    digits = ''.join(c for c in q if c.isdigit())
    if len(digits) >= 7:
        telefono_norm = normalize_telefono(digits)
        qs = Cliente.objects.filter(telefono__contains=telefono_norm)
    else:
        qs = Cliente.objects.filter(nombre__icontains=q)
    clientes = [
        {'id': c.pk, 'nombre': c.nombre, 'telefono': c.telefono, 'descuento': float(c.descuento)}
        for c in qs[:10]
    ]
    return JsonResponse({'clientes': clientes})


@csrf_exempt
@require_POST
def api_pedido_create(request):
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    nombre = str(payload.get('nombre', '')).strip()
    telefono = str(payload.get('telefono', '')).strip()
    items = payload.get('items', [])
    envio = payload.get('envio', 0)
    if not nombre or not telefono:
        return JsonResponse({'error': 'nombre y telefono requeridos'}, status=400)
    if not items:
        return JsonResponse({'error': 'items vacíos'}, status=400)
    try:
        pedido = crear_pedido_bot(
            nombre=nombre,
            telefono=telefono,
            items=items,
            envio=Decimal(str(envio)),
        )
    except (VentaInvalida, Exception) as e:
        return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({
        'ok': True,
        'pedido_id': pedido.pk,
        'total': f'{pedido.total_a_cobrar:.2f}',
    })
