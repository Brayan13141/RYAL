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
    """Los renglones del lote agrupados por modelo y color, como en la cuadrícula.

    Cada grupo trae todas las tallas activas: las que aún no tiene van en 0 y, si se
    capturan, se agregan al lote. Una talla inactiva solo sale si ya tiene pares."""
    tallas = list(TallaCalzado.objects.filter(activa=True))
    grupos = {}
    for f in filas_editables(lote):
        g = grupos.setdefault((f['tipo_id'], f['color_id']), {
            'tipo_id': f['tipo_id'], 'tipo': f['tipo'], 'color': f['color'], 'por_talla': {}})
        g['por_talla'][f['talla_id']] = f
    # Lo anulado por completo deja de ser renglón, pero su modelo·color sigue en el lote.
    for r in (lote.pares.values('tipo_id', 'color_id', 'tipo__nombre', 'color__nombre')
              .distinct().order_by('tipo__nombre', 'color__nombre')):
        grupos.setdefault((r['tipo_id'], r['color_id']), {
            'tipo_id': r['tipo_id'], 'tipo': r['tipo__nombre'], 'color': r['color__nombre'],
            'por_talla': {}})
    salida = []
    for (tipo_id, color_id), g in sorted(grupos.items(),
                                         key=lambda kv: (kv[1]['tipo'], kv[1]['color'])):
        por_talla = g.pop('por_talla')
        filas = []
        for t in tallas:
            f = por_talla.pop(t.pk, None)
            filas.append(_fila(tipo_id, color_id, t.pk, t.mx_cm, f))
        filas += [_fila(tipo_id, color_id, f['talla_id'], f['talla_mx'], f)
                  for f in por_talla.values()]  # tallas inactivas con pares
        g['filas'] = filas
        g['total'] = sum(f['activos'] for f in filas)
        salida.append(g)
    return salida


def _fila(tipo_id, color_id, talla_id, talla_mx, f):
    return {'clave': f'{tipo_id}_{color_id}_{talla_id}', 'talla': fmt_talla(talla_mx),
            'activos': f['activos'] if f else 0, 'minimo': f['minimo'] if f else 0}


def _modelos_del_lote(grupos):
    """Chips de filtro: un modelo por tipo, con cuántos pares suma en el lote."""
    modelos = {}
    for g in grupos:
        m = modelos.setdefault(g['tipo_id'], {'pk': g['tipo_id'], 'nombre': g['tipo'], 'total': 0})
        m['total'] += g['total']
    return sorted(modelos.values(), key=lambda m: m['nombre'])


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
    grupos = _grupos(lote)
    return render(request, 'inventario/lote_editar.html', {
        'lote': lote, 'grupos': grupos, 'modelos_lote': _modelos_del_lote(grupos),
        'version': version_lote(lote),
        'con_precio': lote.origen != 'inicial',
        'modelos': _modelos(), 'tallas': TallaCalzado.objects.filter(activa=True),
        'vias': VIAS,
    })
