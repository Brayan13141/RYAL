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
from .services import crear_pedido_bot, crear_pedido_tienda_bot, VentaInvalida


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
        qs = Cliente.objects.filter(telefono__contains=telefono_norm).order_by('nombre')
    else:
        qs = Cliente.objects.filter(nombre__icontains=q).order_by('nombre')
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
    descuento_monto = payload.get('descuento_monto', 0)
    codigo_descuento_id = payload.get('codigo_descuento_id')
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
            descuento_aplicado=Decimal(str(descuento_monto)),
            codigo_descuento_id=codigo_descuento_id,
        )
    except (VentaInvalida, Exception) as e:
        return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({
        'ok': True,
        'pedido_id': pedido.pk,
        'total': f'{pedido.total_a_cobrar:.2f}',
    })


@csrf_exempt
@ratelimit(key=_client_ip, rate='30/m', block=True)
def api_tienda_create(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'invalid json'}, status=400)
    items = body.get('items') or []
    envio = Decimal(str(body.get('envio', 0)))
    try:
        pedido = crear_pedido_tienda_bot(items=items, envio=envio)
    except VentaInvalida as e:
        return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({
        'ok': True,
        'pedido_id': pedido.pk,
        'total': f'{pedido.precio_venta + pedido.envio:.2f}',
    })


@csrf_exempt
@require_GET
def api_tipos_list(request):
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    from catalog.models import TipoArticulo
    tipos = [{'id': t.pk, 'nombre': t.nombre, 'keywords': t.keywords, 'costo': float(t.costo)}
             for t in TipoArticulo.objects.all()]
    return JsonResponse({'tipos': tipos})


@csrf_exempt
@require_POST
def api_articulo_buscar(request):
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'json inválido'}, status=400)
    descripcion = str(payload.get('descripcion', ''))
    from catalog.services import buscar_tipo_articulo
    tipo = buscar_tipo_articulo(descripcion)
    if tipo:
        return JsonResponse({'match': True, 'nombre': tipo.nombre, 'costo': float(tipo.costo), 'id': tipo.pk})
    return JsonResponse({'match': False, 'nombre': None, 'costo': 0, 'id': None})


@csrf_exempt
@require_POST
def api_codigos_validar(request):
    if not _authorized(request):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'json inválido'}, status=400)
    codigo = str(payload.get('codigo', '')).strip()
    if not codigo:
        return JsonResponse({'error': 'codigo requerido'}, status=400)
    descriptions = list(payload.get('descriptions', []))
    from catalog.services import validar_codigo
    return JsonResponse(validar_codigo(codigo, descriptions))


@csrf_exempt
@require_POST
@ratelimit(key=_client_ip, rate='10/m', block=True)
def api_codigos_validar_publico(request):
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'json inválido'}, status=400)
    codigo = str(payload.get('codigo', '')).strip()
    if not codigo:
        return JsonResponse({'error': 'codigo requerido'}, status=400)
    descriptions = list(payload.get('descriptions', []))
    from catalog.services import validar_codigo
    return JsonResponse(validar_codigo(codigo, descriptions))
