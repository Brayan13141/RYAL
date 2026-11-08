import re

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import TipoArticulo
from inventario import etiquetas
from inventario.models import MovimientoCantidad
from inventario.services import cantidad as svc
from negocio.models import Pedido


def _entero(valor):
    """Entero >= 0 desde un campo del formulario; ValueError si no lo es."""
    valor = (valor or '').strip()
    if not re.fullmatch(r'[0-9]{1,9}', valor):
        raise ValueError('La cantidad debe ser un número entero de 0 o más.')
    return int(valor)


def _tipo_activo(pk):
    return get_object_or_404(TipoArticulo, pk=pk, stock_por_cantidad=True)


@staff_member_required
@require_POST
def cantidad_activar(request):
    try:
        cantidad = _entero(request.POST.get('cantidad'))
        tipo_id = (request.POST.get('tipo_id') or '').strip()
        tipo = (TipoArticulo.objects.filter(pk=int(tipo_id)).first()
                if re.fullmatch(r'[0-9]{1,9}', tipo_id) else None)
        if tipo is None:
            raise ValueError('Elige un tipo de artículo.')
        svc.activar(tipo, cantidad, usuario=request.user)
    except ValueError as e:
        messages.error(request, str(e))
    else:
        messages.success(
            request, f'{tipo.nombre}: stock por cantidad activado con {cantidad} unidades.')
    return redirect('inventario:stock')


@staff_member_required
@require_POST
def cantidad_ajustar(request, pk):
    tipo = _tipo_activo(pk)
    try:
        cantidad = _entero(request.POST.get('cantidad'))
        svc.ajustar(tipo, cantidad, motivo=request.POST.get('motivo'), usuario=request.user)
    except ValueError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f'{tipo.nombre}: stock ajustado a {cantidad}.')
    return redirect('inventario:stock')


@staff_member_required
def cantidad_historial(request, pk):
    tipo = _tipo_activo(pk)
    movimientos = (tipo.movimientos_cantidad.select_related('usuario', 'pedido_item__pedido')
                   .order_by('-pk'))
    return render(request, 'inventario/cantidad_historial.html', {
        'tipo': tipo, 'stock': svc.stock_actual(tipo), 'movimientos': movimientos,
        'AJUSTE': MovimientoCantidad.Clase.AJUSTE, 'CANCELADO': Pedido.CANCELADO})


@staff_member_required
def cantidad_etiqueta(request, pk):
    tipo = _tipo_activo(pk)
    copias = request.GET.get('copias', '1')
    if not re.fullmatch(r'[1-9][0-9]{0,2}', copias) or int(copias) > etiquetas.MAX_POR_PDF:
        return HttpResponseBadRequest(
            f'Copias inválidas: usa un número entero de 1 a {etiquetas.MAX_POR_PDF}.',
            content_type='text/plain; charset=utf-8')
    resp = HttpResponse(
        etiquetas.pdf_etiquetas_tipo(
            tipo, int(copias), base_url=settings.INVENTARIO_QR_BASE_URL),
        content_type='application/pdf')
    modo = 'attachment' if request.GET.get('descargar') == '1' else 'inline'
    resp['Content-Disposition'] = f'{modo}; filename="etiqueta-{svc.codigo_etiqueta(tipo)}.pdf"'
    return resp
