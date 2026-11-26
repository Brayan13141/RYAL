from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.http import require_GET

from inventario import reportes_stock
from inventario.services.descargas import FiltroInvalido, datos_clientes, datos_internos, filtro_de_query
from negocio.views import _XLSX


def _descarga(request, nombre, extension, tipo, generar):
    try:
        filtro = filtro_de_query(request.GET)
    except FiltroInvalido:
        return HttpResponseBadRequest('Filtro inválido.')
    hoy = timezone.localdate()
    resp = HttpResponse(generar(filtro, hoy), content_type=tipo)
    resp['Content-Disposition'] = f'attachment; filename="{nombre}-{hoy:%Y-%m-%d}.{extension}"'
    return resp


@staff_member_required
@require_GET
def stock_pdf_clientes(request):
    return _descarga(request, 'stock-clientes', 'pdf', 'application/pdf',
                     lambda f, hoy: reportes_stock.pdf_clientes(datos_clientes(f), hoy))


@staff_member_required
@require_GET
def stock_pdf_interno(request):
    return _descarga(request, 'stock-interno', 'pdf', 'application/pdf',
                     lambda f, hoy: reportes_stock.pdf_interno(datos_internos(f), hoy))


@staff_member_required
@require_GET
def stock_excel_interno(request):
    return _descarga(request, 'stock-interno', 'xlsx', _XLSX,
                     lambda f, hoy: reportes_stock.excel_interno(datos_internos(f), hoy))
