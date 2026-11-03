from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

from inventario import etiquetas
from inventario.models import Lote, Par


def _pdf(pares, nombre):
    resp = HttpResponse(
        etiquetas.pdf_etiquetas(pares, base_url=settings.INVENTARIO_QR_BASE_URL),
        content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="{nombre}"'
    return resp


@staff_member_required
def etiquetas_lote(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    pares = lote.pares.select_related('tipo__padre', 'color', 'talla').order_by('pk')
    if request.GET.get('solo') == 'pendientes':
        pares = pares.filter(estado=Par.Estado.POR_RECIBIR)
    pares = list(pares)
    if not pares:
        raise Http404('No hay etiquetas por imprimir.')
    return _pdf(pares, f'etiquetas-lote-{lote.pk}.pdf')


@staff_member_required
def etiqueta_par(request, codigo):
    par = get_object_or_404(
        Par.objects.select_related('tipo__padre', 'color', 'talla'), codigo__iexact=codigo)
    return _pdf([par], f'etiqueta-{par.codigo}.pdf')
