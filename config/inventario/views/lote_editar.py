import json
import re

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html

from inventario.models import VIAS, Lote, TallaCalzado, fmt_talla
from inventario.services.editar_lote import editar_lote, filas_editables, version_lote
from inventario.views.lotes import _entero, _error, _lineas_desde_json, _modelos

_CLAVE = re.compile(r'(\d+)_(\d+)_(\d+)')


def _cantidades(crudas):
    """{"tipo_color_talla": n} del JSON -> {(tipo, color, talla): n}; ValueError si no cuadra."""
    if not isinstance(crudas, dict):
        raise ValueError('Solicitud inválida.')
    salida = {}
    for clave, valor in crudas.items():
        m = _CLAVE.fullmatch(str(clave))
        if not m:
            raise ValueError('Solicitud inválida.')
        try:
            salida[tuple(map(int, m.groups()))] = _entero(valor)
        except (ValueError, TypeError):
            raise ValueError('Las cantidades deben ser enteros de 0 o más.')
    return salida


def _grupos(lote):
    """Los renglones del lote agrupados por modelo y color, como en la cuadrícula."""
    grupos = {}
    for f in filas_editables(lote):
        g = grupos.setdefault((f['tipo_id'], f['color_id']), {
            'tipo': f['tipo'], 'color': f['color'], 'filas': []})
        g['filas'].append({
            'clave': f'{f["tipo_id"]}_{f["color_id"]}_{f["talla_id"]}',
            'talla': fmt_talla(f['talla_mx']), 'activos': f['activos'], 'minimo': f['minimo']})
    return list(grupos.values())


def _plural(n, singular, plural):
    return f'{n} {singular if n == 1 else plural}'


def _mensaje(request, lote, res):
    if not res.creados and not res.anulados:
        messages.info(request, 'Sin cambios en el lote.')
        return
    if res.creados:
        codigos = ','.join(p.codigo for p in res.creados)
        url = reverse('inventario:etiquetas_lote', args=[lote.pk]) + f'?codigos={codigos}'
        messages.success(request, format_html(
            '{}: <a href="{}" target="_blank">imprime sus etiquetas</a>.',
            _plural(len(res.creados), 'par agregado', 'pares agregados'), url))
    if res.anulados:
        messages.warning(request, '{}: si ya estaban pegadas, retíralas de la caja. '
                         'Si alguien las escanea, el escáner avisa que están anuladas.'.format(
                             _plural(len(res.anulados), 'etiqueta anulada', 'etiquetas anuladas')))


@staff_member_required
def lote_editar(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    if lote.cerrado_at is not None:
        messages.error(request, 'El lote está cerrado: ya no se puede editar.')
        return redirect('inventario:lote_detalle', pk=lote.pk)
    if request.method == 'POST':
        try:
            datos = json.loads(request.body or b'{}')
            if not isinstance(datos, dict):
                raise ValueError
        except ValueError:
            return _error('Solicitud inválida.')
        version = datos.get('version')
        if not isinstance(version, int) or isinstance(version, bool):
            return _error('Solicitud inválida.')
        crudas = datos.get('lineas') or []
        try:
            cantidades = _cantidades(datos.get('cantidades') or {})
            # Atómico: si la edición falla, tampoco quedan las variantes creadas al vuelo.
            with transaction.atomic():
                lineas = _lineas_desde_json({'lineas': crudas}, lote.via) if crudas else []
                res = editar_lote(lote, cantidades=cantidades, lineas=lineas,
                                  usuario=request.user, version=version)
        except ValueError as e:
            return _error(str(e))
        _mensaje(request, lote, res)
        return JsonResponse({'ok': True, 'url': reverse('inventario:lote_detalle', args=[lote.pk])})
    return render(request, 'inventario/lote_editar.html', {
        'lote': lote, 'grupos': _grupos(lote), 'version': version_lote(lote),
        'con_precio': lote.origen != 'inicial',
        'modelos': _modelos(), 'tallas': TallaCalzado.objects.filter(activa=True),
        'vias': VIAS,
    })
