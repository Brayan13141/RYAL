import base64
import binascii
import hashlib
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from catalog.models import TipoArticulo
from inventario.models import VIAS, Color, Lote, TallaCalzado
from inventario.services.importar import analizar_csv, confirmar_importacion
from inventario.services.lotes import flete_del_lote, leer_flete, tarifas_por_via
from inventario.services.tipos import crear_variante

SALT = 'inventario-import'
MAX_EDAD = 3600  # el token de la preview vale 1 hora
MAX_BYTES = 1024 * 1024



def _form_ctx(via, flete=None):
    tarifas = tarifas_por_via()
    return {'via': via, 'vias': VIAS, 'tarifas': tarifas,
            'flete': tarifas.get(via, '') if flete is None else flete}


def _firmar(contenido, via, ref, flete=None):
    datos = {'b64': base64.b64encode(contenido).decode('ascii'), 'via': via, 'ref': ref}
    if flete is not None:
        datos['flete'] = str(flete)
    return signing.dumps(datos, salt=SALT, compress=True)


def _abrir(token):
    """(contenido, via, ref, flete, None) o (None, None, None, None, mensaje) si el token
    no sirve. `flete` es None en los tokens sin flete capturado (= tarifa de la vía)."""
    try:
        datos = signing.loads(token or '', salt=SALT, max_age=MAX_EDAD)
        contenido = base64.b64decode(datos['b64'])
        via, ref = datos['via'], datos.get('ref', '')
        if via not in VIAS:
            raise KeyError(via)
        flete = leer_flete(datos.get('flete'))
    except signing.SignatureExpired:
        return None, None, None, None, 'La vista previa venció (más de 1 hora). Sube el archivo de nuevo.'
    except (signing.BadSignature, KeyError, TypeError, ValueError, binascii.Error):
        return None, None, None, None, 'La vista previa no es válida. Sube el archivo de nuevo.'
    return contenido, via, ref, flete, None


def _preview(request, contenido, via, ref, flete=None):
    analisis = analizar_csv(contenido, via=via, flete=flete)
    # "Modelo: color" -> (modelo, color) para el botón de asignar.
    sin_asignar = []
    for texto in analisis.faltantes['colores_no_asignados']:
        modelo, _, color = texto.rpartition(': ')
        sin_asignar.append({'texto': texto, 'modelo': modelo, 'color': color})
    return render(request, 'inventario/importar_preview.html', {
        'a': analisis, 'token': _firmar(contenido, via, ref, flete), 'via': via, 'ref': ref,
        'via_display': VIAS[via], 'sin_asignar': sin_asignar})


@staff_member_required
def importar(request):
    if request.method != 'POST':
        return render(request, 'inventario/importar.html', _form_ctx('aereo'))
    via = request.POST.get('via', '')
    ref = (request.POST.get('referencia') or '').strip()[:100]
    flete_txt = (request.POST.get('flete') or '').strip()
    archivo = request.FILES.get('archivo')
    error = flete = None
    if via not in VIAS:
        error = 'Elige la vía: aéreo, marítimo o nacional.'
    elif archivo is None:
        error = 'Elige el archivo CSV.'
    elif archivo.size > MAX_BYTES:
        error = 'El archivo pesa más de 1 MB; revisa que sea el CSV de la llegada.'
    else:
        try:
            flete = leer_flete(flete_txt)
            if flete is not None:
                flete_del_lote(via, flete)  # mismo rango que aceptará el lote
        except ValueError as e:
            error = str(e)
    if error:
        return render(request, 'inventario/importar.html', dict(
            _form_ctx(via if via in VIAS else 'aereo', flete_txt), ref=ref, error=error))
    return _preview(request, archivo.read(), via, ref, flete)


@staff_member_required
@require_POST
def importar_preview(request):
    contenido, via, ref, flete, error = _abrir(request.POST.get('token'))
    if error:
        return HttpResponse(error, status=400)
    return _preview(request, contenido, via, ref, flete)


def _ya_importado(request, lote):
    messages.info(request, f'Este archivo ya se importó: lote #{lote.pk}.')
    return redirect('inventario:lote_detalle', pk=lote.pk)


@staff_member_required
@require_POST
def importar_confirmar(request):
    contenido, via, ref, flete, error = _abrir(request.POST.get('token'))
    if error:
        return HttpResponse(error, status=400)
    sha = hashlib.sha256((request.POST.get('token') or '').encode('utf-8')).hexdigest()
    previo = Lote.objects.filter(token_sha=sha).first()
    if previo is not None:
        return _ya_importado(request, previo)
    try:
        lote = confirmar_importacion(
            contenido, via=via, usuario=request.user, referencia=ref, token_sha=sha,
            flete=flete)
    except ValueError as e:
        return HttpResponse(str(e), status=400)
    except IntegrityError:
        # Dos confirmaciones simultáneas: el UNIQUE dejó pasar solo una.
        previo = Lote.objects.filter(token_sha=sha).first()
        if previo is None:
            raise
        return _ya_importado(request, previo)
    messages.success(
        request, f'Llegada importada: lote #{lote.pk} con {lote.pares.count()} pares por recibir.')
    return redirect('inventario:lote_detalle', pk=lote.pk)


def _json_error(msg):
    return JsonResponse({'ok': False, 'error': msg}, status=400)


def _alta(request, accion):
    """Valida el token y ejecuta `accion(via, post)`; devuelve JSON {ok}."""
    _, via, _, _, error = _abrir(request.POST.get('token'))
    if error:
        return _json_error(error)
    try:
        with transaction.atomic():
            accion(via, request.POST)
    except (ValidationError, ValueError) as e:
        msg = ' '.join(e.messages) if isinstance(e, ValidationError) else str(e)
        return _json_error(msg)
    except IntegrityError:
        return _json_error('Ya existe un registro igual.')
    return JsonResponse({'ok': True})


def _base_por_nombre(nombre):
    base = TipoArticulo.objects.filter(
        padre__isnull=True, nombre__iexact=(nombre or '').strip()).first()
    if base is None:
        raise ValueError(f'No existe el modelo "{nombre}".')
    return base


@staff_member_required
@require_POST
def importar_alta_color(request):
    def accion(via, post):
        nombre = Color.normalizar(post.get('color'))
        if not nombre:
            raise ValueError('Escribe el nombre del color.')
        if len(nombre) > 60:
            raise ValueError('El nombre del color es demasiado largo.')
        base = _base_por_nombre(post.get('modelo')) if post.get('modelo') else None
        color, _ = Color.objects.get_or_create(nombre=nombre)
        if base is not None:
            base.colores.add(color)
    return _alta(request, accion)


@staff_member_required
@require_POST
def importar_alta_variante(request):
    def accion(via, post):
        crear_variante(_base_por_nombre(post.get('modelo')), via)
    return _alta(request, accion)


@staff_member_required
@require_POST
def importar_alta_talla(request):
    def accion(via, post):
        try:
            mx = Decimal((post.get('mx_cm') or '').strip().replace(',', '.'))
        except InvalidOperation:
            raise ValueError('La talla debe ser un número.')
        if (not mx.is_finite() or not (Decimal('10') <= mx <= Decimal('40'))
                or mx != mx.quantize(Decimal('0.1'))):
            raise ValueError('La talla MX debe estar entre 10 y 40, con un decimal como máximo.')
        mx = mx.quantize(Decimal('0.1'))
        if TallaCalzado.objects.filter(mx_cm=mx).exists():
            raise ValueError(f'La talla {mx} ya existe (revisa si está inactiva en Catálogos).')
        # Se inserta en su lugar: las tallas más grandes se recorren una posición.
        previa = TallaCalzado.objects.filter(mx_cm__lt=mx).order_by('-mx_cm').first()
        orden = previa.orden + 1 if previa else 1
        TallaCalzado.objects.filter(orden__gte=orden).update(orden=F('orden') + 1)
        TallaCalzado.objects.create(mx_cm=mx, orden=orden)
    return _alta(request, accion)
