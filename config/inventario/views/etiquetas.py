import re

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404

from inventario import etiquetas
from inventario.models import Lote, Par


def _pdf(request, pares, nombre):
    """PDF de etiquetas: se abre en la pestaña, o se descarga con ?descargar=1."""
    resp = HttpResponse(
        etiquetas.pdf_etiquetas(pares, base_url=settings.INVENTARIO_QR_BASE_URL),
        content_type='application/pdf')
    modo = 'attachment' if request.GET.get('descargar') == '1' else 'inline'
    resp['Content-Disposition'] = f'{modo}; filename="{nombre}"'
    return resp


_CAMPO_GRUPO = re.compile(r'c_(\d+)_(\d+)_(\d+)')


def _seleccion(querydict):
    """[(tipo_id, color_id, talla_id, n)] de los campos c_<tipo>_<color>_<talla>=n, en el
    orden del formulario; None si no viene ningún campo; ValueError si una cantidad no
    es un entero >= 0. Vacío o 0 = no imprimir ese grupo."""
    grupos, hubo = [], False
    for clave in querydict:
        m = _CAMPO_GRUPO.fullmatch(clave)
        if not m:
            continue
        hubo = True
        valor = querydict.get(clave, '').strip()
        if not valor:
            continue
        if not re.fullmatch(r'[0-9]+', valor):
            raise ValueError(valor)
        if int(valor) > 0:
            grupos.append((*map(int, m.groups()), int(valor)))
    return grupos if hubo else None


@staff_member_required
def etiquetas_lote(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    pares = lote.pares.select_related('tipo__padre', 'color', 'talla').order_by('pk')
    if request.GET.get('solo') == 'pendientes':
        pares = pares.filter(estado=Par.Estado.POR_RECIBIR)
    try:
        grupos = _seleccion(request.GET)
    except ValueError:
        # Sin reflejar el valor: viene del GET y la respuesta se abre en el navegador.
        return HttpResponseBadRequest(
            'Cantidad inválida: usa un número entero (0 o más).',
            content_type='text/plain; charset=utf-8')
    if grupos is None:
        pares = list(pares)
    else:
        pares = [p for t, c, z, n in grupos
                 for p in pares.filter(tipo_id=t, color_id=c, talla_id=z)[:n]]
    if not pares:
        raise Http404('No hay etiquetas por imprimir.')
    return _pdf(request, pares, f'etiquetas-lote-{lote.pk}.pdf')


@staff_member_required
def etiqueta_par(request, codigo):
    par = get_object_or_404(
        Par.objects.select_related('tipo__padre', 'color', 'talla'), codigo__iexact=codigo)
    return _pdf(request, [par], f'etiqueta-{par.codigo}.pdf')
