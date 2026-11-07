import re

from django.conf import settings
from django.db.models import Case, When
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


def _posicion(valor):
    """Posición en base 1 desde el GET; None si viene vacía; ValueError si no es entero >= 1."""
    valor = (valor or '').strip()
    if not valor:
        return None
    if not re.fullmatch(r'[1-9][0-9]{0,5}', valor):
        raise ValueError(valor)
    return int(valor)


@staff_member_required
def etiquetas_lote(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    pares = lote.pares.select_related('tipo__padre', 'color', 'talla').order_by('pk')
    if request.GET.get('solo') == 'pendientes':
        pares = pares.filter(estado=Par.Estado.POR_RECIBIR)
    # ?modelo=<tipo_id>: solo ese modelo (el botón de cada card del lote).
    modelo = request.GET.get('modelo')
    if modelo is not None:
        if not re.fullmatch(r'[1-9][0-9]*', modelo):
            return HttpResponseBadRequest(
                'Modelo inválido.', content_type='text/plain; charset=utf-8')
        pares = pares.filter(tipo_id=int(modelo))
    try:
        grupos = _seleccion(request.GET)
    except ValueError:
        # Sin reflejar el valor: viene del GET y la respuesta se abre en el navegador.
        return HttpResponseBadRequest(
            'Cantidad inválida: usa un número entero (0 o más).',
            content_type='text/plain; charset=utf-8')
    valor_parte = request.GET.get('parte')
    if valor_parte is not None and not re.fullmatch(r'[1-9][0-9]*', valor_parte):
        return HttpResponseBadRequest(
            'Parte inválida: usa un número entero desde 1.',
            content_type='text/plain; charset=utf-8')
    try:
        desde, hasta = (_posicion(request.GET.get(k)) for k in ('desde', 'hasta'))
    except ValueError:
        return HttpResponseBadRequest(
            'Intervalo inválido: usa números enteros desde 1.',
            content_type='text/plain; charset=utf-8')
    if desde and hasta and desde > hasta:
        return HttpResponseBadRequest(
            'Intervalo inválido: el inicio es mayor que el final.',
            content_type='text/plain; charset=utf-8')
    hay_intervalo = bool(desde or hasta)
    # ?codigos=A,B: esos pares del lote (p. ej. los de una talla recién corregida).
    codigos = [c.strip().upper() for c in request.GET.get('codigos', '').split(',') if c.strip()]
    if codigos:
        pares = pares.filter(codigo__in=codigos[:etiquetas.MAX_POR_PDF * 10])
        grupos = None
    # Un intervalo sin renglones marcados se toma sobre todo el lote.
    if grupos is None or (not grupos and hay_intervalo):
        pares = list(pares)
    else:
        # De cada renglón salen primero los pares sin escanear: pedir 1 de un renglón
        # con un par ya recibido y otro pendiente imprime el pendiente.
        primero_pend = Case(When(estado=Par.Estado.POR_RECIBIR, then=0), default=1)
        pares = [p for t, c, z, n in grupos
                 for p in pares.filter(tipo_id=t, color_id=c, talla_id=z)
                 .order_by(primero_pend, 'pk')[:n]]
    if hay_intervalo:
        # Numeración en base 1 de la misma lista, la de los botones "Etiquetas d–h".
        pares = pares[(desde or 1) - 1:hasta or len(pares)]
    if not pares:
        raise Http404('No hay etiquetas por imprimir.')
    # Sin ?parte va la primera: nunca sale un PDF que la app de la impresora rechace.
    parte = int(valor_parte or 1)
    tam = etiquetas.MAX_POR_PDF
    trozo = pares[(parte - 1) * tam:parte * tam]
    if not trozo:
        raise Http404('Esa parte no existe.')
    nombre = f'etiquetas-lote-{lote.pk}'
    if hay_intervalo:
        nombre += f'-{desde or 1}-a-{hasta or "fin"}'
    if valor_parte:
        nombre += f'-parte-{parte}'
    nombre += '.pdf'
    return _pdf(request, trozo, nombre)


@staff_member_required
def etiqueta_par(request, codigo):
    par = get_object_or_404(
        Par.objects.select_related('tipo__padre', 'color', 'talla'), codigo__iexact=codigo)
    return _pdf(request, [par], f'etiqueta-{par.codigo}.pdf')
