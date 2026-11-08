import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_POST

from django.conf import settings
from django.utils.safestring import mark_safe

from inventario import etiquetas
from inventario.models import Conteo, Lote, Par, fmt_talla, precio_lista_de
from inventario.services import cambios, conteos, escaneo, ventas
from inventario.validacion import cabe_en
from negocio.models import Pago, Pedido, PedidoItem


def _cuerpo(request):
    """Cuerpo JSON como dict, o None si no es JSON válido."""
    try:
        datos = json.loads(request.body or b'{}')
    except ValueError:
        return None
    return datos if isinstance(datos, dict) else None


def _error(mensaje):
    return JsonResponse({'error': mensaje}, status=400)


def _respuesta(res):
    return JsonResponse({
        'nivel': res.nivel,
        'mensaje': res.mensaje,
        'codigo': res.par.codigo if res.par else None,
        'movimiento_id': res.movimiento_id,
    })


@staff_member_required
def escanear(request):
    lotes = list(Lote.objects.filter(cerrado_at__isnull=True).order_by('-creado_at'))
    try:
        lote_sel = int(request.GET.get('lote', ''))
    except ValueError:
        lote_sel = None
    # Un lote cerrado también se escanea (un faltante puede "aparecer"): entra si viene por URL.
    if lote_sel and lote_sel not in {l.pk for l in lotes}:
        extra = Lote.objects.filter(pk=lote_sel).first()
        if extra:
            lotes.insert(0, extra)
        else:
            lote_sel = None
    try:
        conteo_sel = Conteo.objects.filter(pk=int(request.GET.get('conteo', ''))).first()
    except ValueError:
        conteo_sel = None
    return render(request, 'inventario/escaner.html', {
        'lotes': lotes, 'lote_sel': lote_sel or None, 'conteo_sel': conteo_sel,
        'metodos': Pago.METODO_CHOICES})


@staff_member_required
@require_POST
def escanear_recibir(request):
    datos = _cuerpo(request)
    if datos is None:
        return _error('Solicitud inválida.')
    try:
        lote = Lote.objects.filter(pk=int(datos.get('lote_id'))).first()
    except (TypeError, ValueError):
        lote = None
    if lote is None:
        return _error('Elige un lote válido para recibir.')
    res = escaneo.recibir(str(datos.get('lectura') or ''), lote=lote, usuario=request.user)
    return _respuesta(res)


@staff_member_required
@require_POST
def deshacer(request):
    datos = _cuerpo(request)
    if datos is None:
        return _error('Solicitud inválida.')
    try:
        movimiento_id = int(datos.get('movimiento_id'))
    except (TypeError, ValueError):
        return _error('Lectura inválida.')
    res = escaneo.deshacer(
        movimiento_id, usuario=request.user, nota=str(datos.get('nota') or ''))
    return _respuesta(res)


@staff_member_required
def par_ficha(request, codigo):
    par = get_object_or_404(
        Par.objects.select_related(
            'tipo__padre', 'color', 'talla', 'lote', 'pedido_item'),
        codigo__iexact=codigo)
    movimientos = par.movimientos.select_related('usuario').order_by('creado_at', 'pk')
    return render(request, 'inventario/par_ficha.html', {
        'par': par, 'talla': fmt_talla(par.talla.mx_cm), 'movimientos': movimientos,
        # Réplica de la etiqueta: el mismo QR y las mismas equivalencias que se imprimen.
        'qr_svg': mark_safe(etiquetas.qr_svg(
            etiquetas.url_par(settings.INVENTARIO_QR_BASE_URL, par.codigo))),
        'equivalencias': etiquetas.equivalencias(par.talla)})


def _dos_decimales(valor):
    return f'{valor:.2f}'


def _items_bot_json(par):
    return [{
        'id': item.pk, 'pedido_id': item.pedido_id, 'fecha': item.pedido.fecha.isoformat(),
        'nombre_snapshot': item.nombre_snapshot, 'libres': libres}
        for item, libres in ventas.items_bot_pendientes(par)]


def _vendido_por_bot_con_hermano(par):
    """True si el par lo vendió el bot solo y hay otro igual en stock (intercambio posible)."""
    return (par.estado == Par.Estado.VENDIDO and ventas.asignado_por_bot(par)
            and Par.objects.filter(
                tipo_id=par.tipo_id, color_id=par.color_id, talla_id=par.talla_id,
                estado=Par.Estado.EN_STOCK).exists())


@staff_member_required
@require_POST
def vender_info(request):
    """Ficha de un par para el carrito de Vender: datos + precios del modelo base."""
    datos = _cuerpo(request)
    if datos is None:
        return _error('Solicitud inválida.')
    codigo = escaneo.extraer_codigo(str(datos.get('lectura') or ''))
    par = (Par.objects.select_related('tipo__padre', 'color', 'talla')
           .filter(codigo=codigo).first()) if codigo else None
    if par is None:
        return JsonResponse({'nivel': 'rojo', 'mensaje': 'Código no encontrado'})
    falta_llegada = ventas.sin_recibir(par)
    if par.estado != Par.Estado.EN_STOCK and not falta_llegada:
        if _vendido_por_bot_con_hermano(par):
            return JsonResponse({
                'nivel': 'amarillo', 'codigo': par.codigo,
                'mensaje': ('Ya vendido por el bot. Si el par que sacaste es otro de la misma '
                            'variante, escanéalo y elige esa venta para intercambiarlo.'),
                'items_bot': (
                    [f for f in _items_bot_json(par) if f['id'] == par.pedido_item_id])})
        return JsonResponse({
            'nivel': 'rojo', 'codigo': par.codigo,
            'mensaje': f'No se puede vender: el par está en estado {par.get_estado_display()}'})
    base = par.tipo.modelo_base
    extra = {}
    if falta_llegada:
        # Se puede vender, pero el vendedor confirma que el par sí llegó.
        extra = {'sin_recibir': True, 'mensaje': (
            f'Este par no se ha registrado como recibido (lote #{par.lote_id}). '
            '¿Llegó y lo vas a vender? Se registra su llegada y su venta al cobrar.')}
    return JsonResponse({
        'nivel': 'amarillo' if falta_llegada else 'verde',
        **extra,
        'par': {
            'codigo': par.codigo, 'nombre': base.nombre, 'color': par.color.nombre,
            # Sin costo: el cliente puede ver esta pantalla. El costo vive en la ficha del par.
            'talla': fmt_talla(par.talla.mx_cm)},
        'precios': [{'nombre': pv.nombre, 'precio': _dos_decimales(pv.precio)}
                    for pv in base.precios_venta.order_by('orden', 'pk')],
        # Con este precio se llena el par según el selector Menudeo / Mayoreo.
        'precio_lista': {tv: _dos_decimales(p) for tv, p in precio_lista_de(par).items()},
        'items_bot': _items_bot_json(par),
    })


@staff_member_required
@require_POST
def vender_ligar(request):
    """'Ya se vendió por el bot': liga (o intercambia) el par escaneado con un ítem del bot."""
    datos = _cuerpo(request)
    if datos is None:
        return _error('Solicitud inválida.')
    try:
        item_id = int(datos.get('item_id'))
    except (TypeError, ValueError):
        return _error('Elige una venta del bot.')
    codigo = escaneo.extraer_codigo(str(datos.get('codigo') or ''))
    par = Par.objects.filter(codigo=codigo).first() if codigo else None
    if par is None:
        return JsonResponse({'nivel': 'rojo', 'mensaje': 'Código no encontrado'})
    # Solo vale un ítem que la lista de pendientes ofrece para ESTE par.
    item = next((i for i, _ in ventas.items_bot_pendientes(par) if i.pk == item_id), None)
    if item is None:
        return _error('Esa venta del bot no corresponde a este par.')
    recibir = datos.get('recibir', False)
    if not isinstance(recibir, bool):
        return _error('Solicitud inválida.')
    return _respuesta(ventas.ligar_a_item(par, item, usuario=request.user, recibir=recibir))


def _error_venta(mensaje):
    return JsonResponse({'ok': False, 'error': mensaje}, status=400)


def _precio(valor):
    """Decimal finito >= 0 que cabe en el precio del ítem, o None."""
    if valor is None or isinstance(valor, bool) or str(valor).strip() == '':
        return None
    try:
        precio = Decimal(str(valor).strip())
    except InvalidOperation:
        return None
    if not precio.is_finite() or precio < 0:
        return None
    return precio if cabe_en(PedidoItem._meta.get_field('precio_unitario'), precio) else None


@staff_member_required
@require_POST
def vender_cerrar(request):
    datos = _cuerpo(request)
    if datos is None:
        return _error_venta('Solicitud inválida.')
    crudas = datos.get('lineas')
    if not isinstance(crudas, list) or not crudas:
        return _error_venta('El carrito está vacío.')
    metodo = datos.get('metodo_pago')
    if metodo not in {m[0] for m in Pago.METODO_CHOICES}:
        return _error_venta('Elige un método de pago válido.')
    tipo_venta = datos.get('tipo_venta')
    if not isinstance(tipo_venta, str) or tipo_venta not in {
            t[0] for t in Pedido.TIPO_VENTA_CHOICES}:
        return _error_venta('Elige si la venta es de menudeo o de mayoreo.')
    lineas, vistos, recibir = [], set(), set()
    for cruda in crudas:
        if not isinstance(cruda, dict):
            return _error_venta('Solicitud inválida.')
        codigo = escaneo.extraer_codigo(str(cruda.get('codigo') or ''))
        if codigo in vistos:
            return _error_venta(f'El par {codigo} está repetido.')
        vistos.add(codigo)
        precio = _precio(cruda.get('precio'))
        if precio is None:
            return _error_venta(f'Precio inválido para {codigo or "un par"}.')
        par = Par.objects.select_related('tipo', 'color', 'talla').filter(codigo=codigo).first()
        if par is None:
            return _error_venta(f'El par {codigo} no existe.')
        marca = cruda.get('recibir', False)
        if not isinstance(marca, bool):
            return _error_venta('Solicitud inválida.')
        if marca:
            recibir.add(par.pk)
        lineas.append((par, precio))
    if datos.get('confirmar_venta_nueva') is not True:
        # Si el bot ya registró esa venta, escanear y cobrar de nuevo sería cobrarla dos veces.
        for par, _precio_par in lineas:
            if any(libres > 0 for _item, libres in ventas.items_bot_pendientes(par)):
                return JsonResponse({
                    'ok': False, 'requiere_confirmacion': True,
                    'error': (f'El bot ya registró una venta de {par.tipo.nombre} '
                              f'{par.color.nombre} {fmt_talla(par.talla.mx_cm)}. '
                              '¿Es una venta distinta?')}, status=409)
    try:
        pedido = ventas.vender_pares(
            lineas, metodo_pago=metodo, usuario=request.user, tipo_venta=tipo_venta,
            recibir=recibir)
    except ValueError as e:
        return _error_venta(str(e))
    return JsonResponse({'ok': True, 'pedido_id': pedido.pk})


# ── Cambio de par ────────────────────────────────────────────────────────────

def _par_json(par):
    # Sin costo: el cliente puede ver esta pantalla.
    return {'codigo': par.codigo, 'nombre': par.tipo.modelo_base.nombre,
            'color': par.color.nombre, 'talla': fmt_talla(par.talla.mx_cm)}


def _par_por_lectura(lectura):
    codigo = escaneo.extraer_codigo(str(lectura or ''))
    if not codigo:
        return None
    return (Par.objects.select_related('tipo__padre', 'color', 'talla', 'lote',
                                       'pedido_item__pedido')
            .filter(codigo=codigo).first())


def _venta_del_par(par):
    """El ítem de venta (no cancelada) donde salió el par, o un mensaje de por qué no."""
    if par.estado != Par.Estado.VENDIDO or par.pedido_item is None:
        return None, f'Este par no está vendido (estado {par.get_estado_display()})'
    if par.pedido_item.pedido.estado == Pedido.CANCELADO:
        return None, 'La venta de este par está cancelada'
    return par.pedido_item, None


@staff_member_required
@require_POST
def cambio_regresa(request):
    """Par que el cliente regresa: tiene que estar vendido en una venta vigente."""
    datos = _cuerpo(request)
    if datos is None:
        return _error('Solicitud inválida.')
    par = _par_por_lectura(datos.get('lectura'))
    if par is None:
        return JsonResponse({'nivel': 'rojo', 'mensaje': 'Código no encontrado'})
    item, motivo = _venta_del_par(par)
    if item is None:
        return JsonResponse({'nivel': 'rojo', 'codigo': par.codigo, 'mensaje': motivo})
    pedido = item.pedido
    return JsonResponse({
        'nivel': 'verde', 'par': _par_json(par),
        'venta': {'pedido_id': pedido.pk, 'fecha': pedido.fecha.isoformat(),
                  'precio_pagado': _dos_decimales(item.precio_unitario),
                  'tipo_venta': pedido.tipo_venta}})


@staff_member_required
@require_POST
def cambio_nuevo(request):
    """Par que se lleva: tiene que estar en stock. Sugiere el precio de lista de la venta."""
    datos = _cuerpo(request)
    if datos is None:
        return _error('Solicitud inválida.')
    par = _par_por_lectura(datos.get('lectura'))
    if par is None:
        return JsonResponse({'nivel': 'rojo', 'mensaje': 'Código no encontrado'})
    if par.estado != Par.Estado.EN_STOCK:
        return JsonResponse({
            'nivel': 'rojo', 'codigo': par.codigo,
            'mensaje': f'No se puede llevar: el par está en estado {par.get_estado_display()}'})
    regresa = _par_por_lectura(datos.get('regresa'))
    item = _venta_del_par(regresa)[0] if regresa else None
    if item is None:
        return _error('Escanea primero el par que regresa.')
    sugerido = precio_lista_de(par).get(item.pedido.tipo_venta) or item.precio_unitario
    return JsonResponse({'nivel': 'verde', 'par': _par_json(par),
                         'precio_sugerido': _dos_decimales(sugerido)})


@staff_member_required
@require_POST
def cambio_confirmar(request):
    datos = _cuerpo(request)
    if datos is None:
        return _error_venta('Solicitud inválida.')
    regresa = _par_por_lectura(datos.get('regresa'))
    nuevo = _par_por_lectura(datos.get('nuevo'))
    if regresa is None or nuevo is None:
        return _error_venta('Escanea el par que regresa y el que se lleva.')
    precio = _precio(datos.get('precio'))
    if precio is None:
        return _error_venta('Precio inválido.')
    try:
        res = cambios.cambiar_par(
            regresa, nuevo, precio=precio, metodo_pago=datos.get('metodo_pago'),
            usuario=request.user, danado=datos.get('danado') is True,
            nota=str(datos.get('nota') or ''))
    except ValueError as e:
        return _error_venta(str(e))
    return JsonResponse({'ok': True, 'pedido_id': res.pedido.pk,
                         'diferencia': _dos_decimales(res.diferencia)})


def _volver_al_pedido(request, pedido_pk):
    return redirect('negocio:pedido_detail', pedido_pk)


@staff_member_required
@require_POST
def venta_cancelar(request, pedido_pk):
    pedido = get_object_or_404(Pedido, pk=pedido_pk)
    try:
        ventas.cancelar_pedido(
            pedido, usuario=request.user, nota=request.POST.get('nota', ''))
    except ValueError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Venta cancelada; los pares volvieron a stock.')
    return _volver_al_pedido(request, pedido.pk)


@staff_member_required
@require_POST
def venta_quitar_par(request, codigo):
    par = get_object_or_404(Par.objects.select_related('pedido_item'), codigo__iexact=codigo)
    pedido_pk = par.pedido_item.pedido_id if par.pedido_item_id else None
    try:
        ventas.quitar_par_de_pedido(
            par, usuario=request.user, nota=request.POST.get('nota', ''))
    except ValueError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f'Par {par.codigo} quitado de la venta.')
    if pedido_pk is None:
        return redirect('inventario:par_ficha', par.codigo)
    return _volver_al_pedido(request, pedido_pk)


# ── Conteos ──────────────────────────────────────────────────────────────────

@staff_member_required
@require_POST
def escanear_contar(request):
    datos = _cuerpo(request)
    if datos is None:
        return _error('Solicitud inválida.')
    try:
        conteo = Conteo.objects.filter(pk=int(datos.get('conteo_id'))).first()
    except (TypeError, ValueError):
        conteo = None
    if conteo is None:
        return _error('Inicia un conteo para contar.')
    res = escaneo.contar(str(datos.get('lectura') or ''), conteo=conteo, usuario=request.user)
    return _respuesta(res)


@staff_member_required
def conteos_lista(request):
    return render(request, 'inventario/conteos.html', {
        'conteos': Conteo.objects.select_related('creado_por')})


@staff_member_required
@require_POST
def conteo_crear(request):
    """Continúa el conteo abierto más reciente, o abre uno nuevo."""
    conteo = Conteo.objects.filter(cerrado_at__isnull=True).first()
    if conteo is None:
        conteo = Conteo.objects.create(creado_por=request.user)
    return redirect(f"{reverse('inventario:escanear')}?conteo={conteo.pk}")


@staff_member_required
@require_POST
def conteo_cerrar(request, pk):
    conteo = get_object_or_404(Conteo, pk=pk)
    if conteo.cerrado_at is None:
        conteo.cerrado_at = timezone.now()
        conteo.notas = request.POST.get('notas', conteo.notas)
        conteo.save(update_fields=['cerrado_at', 'notas'])
    return redirect('inventario:conteo_resumen', conteo.pk)


@staff_member_required
def conteo_resumen(request, pk):
    conteo = get_object_or_404(Conteo, pk=pk)
    resumen = conteos.resumen_conteo(conteo)
    resumen['sin_diferencia'] = not (resumen['no_leidos'] or resumen['fuera_de_stock']
                                     or resumen['desconocidos'])
    return render(request, 'inventario/conteo_resumen.html', {
        'conteo': conteo, **resumen})


@staff_member_required
@require_POST
def conteo_corregir(request, pk):
    conteo = get_object_or_404(Conteo, pk=pk)
    res = conteos.corregir(
        conteo, request.POST.get('codigo', ''), request.POST.get('accion', ''),
        request.POST.get('nota', ''), usuario=request.user)
    (messages.success if res.nivel == 'verde' else messages.error)(
        request, f'{res.par.codigo + ": " if res.par else ""}{res.mensaje}')
    return redirect('inventario:conteo_resumen', conteo.pk)
