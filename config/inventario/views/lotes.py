import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from catalog.models import TipoArticulo
from inventario.models import Color, Lote, Par, TallaCalzado, fmt_talla
from inventario.services.lotes import cerrar_lote, crear_lote
from inventario.validacion import cabe_en

ESTADOS = ['por_recibir', 'en_stock', 'vendido', 'baja']


MAX_POR_TALLA = 500


def _entero(v):
    """Entero >= 0 desde un int o un string de dígitos; ValueError si es float, bool, negativo o basura."""
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    if not isinstance(v, int) or v < 0:
        raise ValueError
    return v


def _error(msg):
    return JsonResponse({'ok': False, 'error': msg}, status=400)


def _respuesta_lote(lote, duplicado=False):
    datos = {'ok': True, 'lote_id': lote.pk,
             'url': reverse('inventario:lote_detalle', args=[lote.pk])}
    if duplicado:
        datos['duplicado'] = True
    return JsonResponse(datos)


def _variantes():
    # Solo variantes con prefijo: sin él no se puede generar el código del par.
    return (TipoArticulo.objects.filter(
        padre__isnull=False, prefijo__isnull=False, usa_tallas=True)
            .exclude(prefijo='').order_by('nombre'))


def _lineas_desde_json(datos):
    """Convierte el JSON del formulario en líneas para `crear_lote`; ValueError si es inválido."""
    lineas = []
    crudas = datos.get('lineas') or []
    if not isinstance(crudas, list):
        raise ValueError('Solicitud inválida.')
    for n, l in enumerate(crudas, 1):
        if not isinstance(l, dict) or not isinstance(l.get('cantidades') or {}, dict):
            raise ValueError(f'Línea {n}: formato inválido.')
        try:
            tipo = TipoArticulo.objects.get(pk=l.get('tipo_id'))
            color = Color.objects.get(pk=l.get('color_id'))
        except (TipoArticulo.DoesNotExist, Color.DoesNotExist, ValueError, TypeError):
            raise ValueError(f'Línea {n}: tipo o color inexistente.')
        crudo = l.get('precio_proveedor')
        try:
            precio = None if crudo in (None, '') else Decimal(str(crudo))
            if precio is not None and not precio.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            raise ValueError(f'Línea {n}: el precio del proveedor no es un número válido.')
        if precio is not None and not cabe_en(Par._meta.get_field('precio_proveedor'), precio):
            raise ValueError(f'Línea {n}: el precio del proveedor es demasiado grande.')
        for talla_txt, cant in (l.get('cantidades') or {}).items():
            if cant in (None, '', 0, '0'):
                continue
            try:
                talla = TallaCalzado.objects.get(mx_cm=Decimal(str(talla_txt)))
                cantidad = _entero(cant)
            except (InvalidOperation, TallaCalzado.DoesNotExist):
                raise ValueError(f'Línea {n}: la talla {talla_txt} no existe.')
            except (ValueError, TypeError):
                raise ValueError(f'Línea {n}: cantidad inválida para la talla {talla_txt}.')
            if cantidad > MAX_POR_TALLA:
                raise ValueError(
                    f'Línea {n}: cantidad demasiado grande para la talla {talla_txt} (máximo {MAX_POR_TALLA}).')
            if cantidad == 0:
                continue
            lineas.append({'tipo': tipo, 'color': color, 'talla': talla,
                           'cantidad': cantidad, 'precio_proveedor': precio})
    if not lineas:
        raise ValueError('Captura al menos un par (cantidad mayor que cero).')
    return lineas


@staff_member_required
def alta_manual(request):
    if request.method == 'POST':
        try:
            datos = json.loads(request.body or b'{}')
            if not isinstance(datos, dict):
                raise ValueError
        except ValueError:
            return _error('Solicitud inválida.')
        via, origen = datos.get('via'), datos.get('origen')
        if via not in ('aereo', 'maritimo') or origen not in ('manual', 'inicial'):
            return _error('Vía u origen inválidos.')
        referencia = datos.get('referencia') or ''
        if not isinstance(referencia, str):
            return _error('La referencia debe ser texto.')
        referencia = referencia.strip()[:100]
        client_token = datos.get('client_token')
        sha = None
        if isinstance(client_token, str) and client_token.strip():
            sha = hashlib.sha256(client_token.strip().encode('utf-8')).hexdigest()
            existente = Lote.objects.filter(token_sha=sha).first()
            if existente is not None:
                return _respuesta_lote(existente, duplicado=True)
        try:
            lineas = _lineas_desde_json(datos)
            lote = crear_lote(
                via=via, origen=origen, lineas=lineas, usuario=request.user,
                referencia=referencia, token_sha=sha)
        except ValueError as e:
            return _error(str(e))
        except IntegrityError:
            # Dos envíos simultáneos: el UNIQUE dejó pasar solo uno.
            existente = Lote.objects.filter(token_sha=sha).first() if sha else None
            if existente is None:
                raise
            return _respuesta_lote(existente, duplicado=True)
        return _respuesta_lote(lote)
    return render(request, 'inventario/alta_manual.html', {
        'variantes': _variantes(),
        'tallas': TallaCalzado.objects.filter(activa=True),
    })


@staff_member_required
def tipo_colores(request, pk):
    tipo = get_object_or_404(TipoArticulo, pk=pk)
    colores = tipo.colores_efectivos().order_by('nombre')
    return JsonResponse({'colores': [{'id': c.pk, 'nombre': c.nombre} for c in colores]})


@staff_member_required
def lotes(request):
    qs = Lote.objects.annotate(
        total=Count('pares'),
        recibidos=Count('pares', filter=Q(pares__estado=Par.Estado.EN_STOCK)))
    return render(request, 'inventario/lotes_lista.html', {'lotes': qs[:200]})


@staff_member_required
def lote_detalle(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    conteo = {e: 0 for e in ESTADOS}
    for fila in lote.pares.values('estado').annotate(n=Count('pk')):
        conteo[fila['estado']] = fila['n']
    filas = [{
        'tipo': r['tipo__nombre'], 'color': r['color__nombre'],
        'talla': fmt_talla(r['talla__mx_cm']), 'esperados': r['esperados'],
        'recibidos': r['recibidos'],
        # Campo de "cuántas imprimir" de este grupo (lo lee views/etiquetas.py).
        'campo': f'c_{r["tipo_id"]}_{r["color_id"]}_{r["talla_id"]}',
    } for r in lote.pares.values('tipo_id', 'color_id', 'talla_id',
                                 'tipo__nombre', 'color__nombre', 'talla__mx_cm')
        .annotate(esperados=Count('pk'),
                  recibidos=Count('pk', filter=Q(estado=Par.Estado.EN_STOCK)))
        .order_by('tipo__nombre', 'color__nombre', 'talla__orden')]
    return render(request, 'inventario/lote_detalle.html', {
        'lote': lote, 'conteo': conteo, 'filas': filas,
        'total': sum(conteo.values())})


@staff_member_required
@require_POST
def lote_cerrar(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    n = cerrar_lote(lote, usuario=request.user)
    plural = 's' if n != 1 else ''
    messages.success(request, f'Lote cerrado: {n} par{"es" if n != 1 else ""} marcado{plural} como faltante.')
    return redirect('inventario:lote_detalle', pk=lote.pk)
