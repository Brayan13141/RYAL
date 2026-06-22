import hmac

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from .models import Cliente
from .phone import normalize_telefono


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
