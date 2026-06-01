import hmac

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt

from .models import Cliente


def _authorized(request):
    # Comparacion en tiempo constante para evitar timing attacks sobre la API key
    auth = request.META.get('HTTP_AUTHORIZATION', '')
    expected = f'Bearer {settings.NEGOCIO_API_KEY}'
    return hmac.compare_digest(auth, expected)


@require_GET
def api_cliente(request, telefono):
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        cliente = Cliente.objects.get(telefono=telefono)
        descuento = float(cliente.descuento)
    except Cliente.DoesNotExist:
        # Cliente no registrado = sin descuento (el bot aplica precio base)
        descuento = 0.0
    return JsonResponse({'descuento': descuento})


@csrf_exempt
def api_log(request):
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    return JsonResponse({'ok': True})
