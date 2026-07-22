import json
from decimal import Decimal
from urllib.parse import quote
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.core.signing import Signer, BadSignature
import datetime
from django.db.models import Q, Count, Sum
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST

from django_ratelimit.decorators import ratelimit

from catalog.models import Category, Product
from .forms import ClienteForm, PedidoForm, PagoForm, GastoForm, PedidoItemForm
from .models import Cliente, Pedido, Pago, Gasto, PedidoItem
from .utils import _mes_range, _MESES_ES, _GANANCIA_EXPR, _VENDIDO_EXPR


def _sync_estado_pedido(pedido):
    """Marca el pedido como pagado o pendiente según balance real."""
    pedido.refresh_from_db()
    if pedido.balance_pendiente <= 0 and pedido.estado == Pedido.PENDIENTE:
        pedido.estado = Pedido.PAGADO
        pedido.save(update_fields=['estado'])
    elif pedido.balance_pendiente > 0 and pedido.estado == Pedido.PAGADO:
        pedido.estado = Pedido.PENDIENTE
        pedido.save(update_fields=['estado'])


def _sync_totales_pedido(pedido):
    """Recalcula precio_venta y costo_producto desde los PedidoItems."""
    items = list(pedido.items.all())
    if items:
        pedido.precio_venta = sum(i.precio_unitario * i.cantidad for i in items)
        pedido.costo_producto = sum(i.costo_unitario * i.cantidad for i in items)
        pedido.save(update_fields=['precio_venta', 'costo_producto'])
from .print_utils import _build_label_json, _build_receipt_json
from .services import crear_venta_tienda, VentaInvalida


# ── Clientes ──────────────────────────────────────────────

@staff_member_required
def clientes_list(request):
    clientes = Cliente.objects.prefetch_related('pedidos__pagos').all()
    for c in clientes:
        pedidos_activos = [p for p in c.pedidos.all() if p.estado == Pedido.PENDIENTE]
        c.balance_total = sum(p.balance_pendiente for p in pedidos_activos)
    return render(request, 'negocio/clientes.html', {'clientes': clientes})


@staff_member_required
def cliente_create(request):
    form = ClienteForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect('negocio:clientes_list')
    return render(request, 'negocio/cliente_form.html', {
        'form': form, 'titulo': 'Nuevo cliente'
    })


@staff_member_required
def cliente_edit(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    form = ClienteForm(request.POST or None, instance=cliente)
    if form.is_valid():
        form.save()
        return redirect('negocio:cliente_detail', pk=pk)
    return render(request, 'negocio/cliente_form.html', {
        'form': form, 'titulo': f'Editar — {cliente.nombre}'
    })


@staff_member_required
def cliente_detail(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    pedidos = cliente.pedidos.prefetch_related('pagos').all()
    return render(request, 'negocio/cliente_detail.html', {
        'cliente': cliente, 'pedidos': pedidos
    })


# ── Pedidos ───────────────────────────────────────────────

@staff_member_required
def pedidos_list(request):
    pedidos = Pedido.objects.select_related('cliente').prefetch_related('pagos').all()
    desde = (request.GET.get('desde') or '').strip()
    hasta = (request.GET.get('hasta') or '').strip()
    if desde:
        pedidos = pedidos.filter(fecha__gte=desde)
    if hasta:
        pedidos = pedidos.filter(fecha__lte=hasta)
    return render(request, 'negocio/pedidos.html',
                  {'pedidos': pedidos, 'desde': desde, 'hasta': hasta})


@staff_member_required
def pedido_create(request):
    form = PedidoForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect('negocio:pedidos_list')
    return render(request, 'negocio/pedido_form.html', {
        'form': form, 'titulo': 'Nuevo pedido'
    })


@staff_member_required
def pedido_detail(request, pk):
    pedido = get_object_or_404(
        Pedido.objects.prefetch_related('pagos', 'items'), pk=pk
    )
    pago_form = PagoForm()
    gasto_form = GastoForm(initial={
        'fecha': pedido.fecha,
        'monto': pedido.costo_producto,
        'categoria': Gasto.COMPRA_PROVEEDOR,
        'descripcion': f'Compra proveedor — Pedido #{pedido.pk}',
    })
    gastos = Gasto.objects.filter(descripcion__icontains=f'Pedido #{pedido.pk}').order_by('-fecha')
    return render(request, 'negocio/pedido_detail.html', {
        'pedido': pedido,
        'pago_form': pago_form,
        'gasto_form': gasto_form,
        'gastos': gastos,
    })


@staff_member_required
@require_POST
def pedido_pago_add(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk)
    form = PagoForm(request.POST)
    if form.is_valid():
        pago = form.save(commit=False)
        pago.pedido = pedido
        pago.save()
        pedido.refresh_from_db()
        _sync_estado_pedido(pedido)
        pedido.refresh_from_db()
        return JsonResponse({
            'ok': True,
            'balance_pendiente': str(pedido.balance_pendiente),
            'estado': pedido.estado,
            'estado_display': pedido.get_estado_display(),
        })
    return JsonResponse({'ok': False, 'errors': form.errors}, status=400)


@staff_member_required
def pago_edit(request, pk):
    pago = get_object_or_404(Pago.objects.select_related('pedido'), pk=pk)
    form = PagoForm(request.POST or None, instance=pago)
    if form.is_valid():
        form.save()
        _sync_estado_pedido(pago.pedido)
        return redirect('negocio:pedido_detail', pk=pago.pedido_id)
    return render(request, 'negocio/pago_form.html', {
        'form': form,
        'pago': pago,
        'pedido': pago.pedido,
    })


@staff_member_required
def pedido_item_edit(request, pk):
    item = get_object_or_404(PedidoItem.objects.select_related('pedido'), pk=pk)
    form = PedidoItemForm(request.POST or None, instance=item)
    if form.is_valid():
        form.save()
        _sync_totales_pedido(item.pedido)
        _sync_estado_pedido(item.pedido)
        return redirect('negocio:pedido_detail', pk=item.pedido_id)
    return render(request, 'negocio/pedido_item_form.html', {
        'form': form,
        'item': item,
        'pedido': item.pedido,
    })


# ── Gastos ────────────────────────────────────────────────

@staff_member_required
@require_POST
def pedido_gasto_add(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk)
    form = GastoForm(request.POST)
    if form.is_valid():
        form.save()
    return redirect('negocio:pedido_detail', pk=pk)


@staff_member_required
def gastos_list(request):
    form = GastoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('negocio:gastos_list')
    gastos = Gasto.objects.all()
    desde = (request.GET.get('desde') or '').strip()
    hasta = (request.GET.get('hasta') or '').strip()
    if desde:
        gastos = gastos.filter(fecha__gte=desde)
    if hasta:
        gastos = gastos.filter(fecha__lte=hasta)
    return render(request, 'negocio/gastos.html',
                  {'gastos': gastos, 'form': form, 'desde': desde, 'hasta': hasta})


@staff_member_required
def pagos_list(request):
    pagos = Pago.objects.select_related('pedido', 'pedido__cliente').all()
    desde = (request.GET.get('desde') or '').strip()
    hasta = (request.GET.get('hasta') or '').strip()
    if desde:
        pagos = pagos.filter(fecha__gte=desde)
    if hasta:
        pagos = pagos.filter(fecha__lte=hasta)
    total = pagos.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    metodo_labels = dict(Pago.METODO_CHOICES)
    por_metodo = [
        {'metodo': r['metodo_pago'], 'label': metodo_labels.get(r['metodo_pago'], r['metodo_pago'].title()),
         'total': r['t']}
        for r in pagos.values('metodo_pago').annotate(t=Sum('monto')).order_by('-t')
    ]
    return render(request, 'negocio/pagos.html', {
        'pagos': pagos.order_by('-fecha', '-id'), 'total': total,
        'por_metodo': por_metodo, 'desde': desde, 'hasta': hasta,
    })


# ── Resumen ───────────────────────────────────────────────


@staff_member_required
def resumen(request):
    hoy = datetime.date.today()
    mes = request.GET.get('mes', f"{hoy.year}-{hoy.month:02d}")
    todo = (mes == 'todo')

    if not todo:
        try:
            y, m = map(int, mes.split('-'))
            fecha_ini, fecha_fin = _mes_range(y, m)
            periodo_label = f"{_MESES_ES[m-1]} {y}"
        except (ValueError, AttributeError):
            mes = f"{hoy.year}-{hoy.month:02d}"
            todo = False
            y, m = hoy.year, hoy.month
            fecha_ini, fecha_fin = _mes_range(y, m)
            periodo_label = f"{_MESES_ES[m-1]} {y}"
        pedido_base = Pedido.objects.filter(fecha__gte=fecha_ini, fecha__lt=fecha_fin)
        gastos_base = Gasto.objects.filter(fecha__gte=fecha_ini, fecha__lt=fecha_fin)
        pagos_base = Pago.objects.filter(fecha__gte=fecha_ini, fecha__lt=fecha_fin)
    else:
        periodo_label = 'Todo el tiempo'
        pedido_base = Pedido.objects.all()
        gastos_base = Gasto.objects.all()
        pagos_base = Pago.objects.all()

    # KPIs principales
    agg_ventas = pedido_base.filter(estado=Pedido.PAGADO).aggregate(
        vendido=Sum(_VENDIDO_EXPR),
        ganancia=Sum(_GANANCIA_EXPR),
        costo=Sum('costo_producto'),
    )
    total_vendido = agg_ventas['vendido'] or Decimal('0')
    total_ganancia = agg_ventas['ganancia'] or Decimal('0')
    total_costo = agg_ventas['costo'] or Decimal('0')

    total_cobrado = pagos_base.aggregate(t=Sum('monto'))['t'] or Decimal('0')

    total_gastos = gastos_base.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    ganancia_neta = total_ganancia - total_gastos
    margen_pct = round(total_ganancia / total_vendido * 100, 1) if total_vendido > 0 else Decimal('0')

    # Cobros por método de pago
    cobros_metodo = list(
        pagos_base.values('metodo_pago').annotate(total=Sum('monto')).order_by('-total')
    )
    metodo_labels = dict(Pago.METODO_CHOICES)
    for row in cobros_metodo:
        row['label'] = metodo_labels.get(row['metodo_pago'], row['metodo_pago'].title())

    # Gastos por categoría
    gastos_cat = list(
        gastos_base.values('categoria').annotate(total=Sum('monto')).order_by('-total')
    )
    cat_labels = dict(Gasto.CATEGORIA_CHOICES)
    for row in gastos_cat:
        row['label'] = cat_labels.get(row['categoria'], row['categoria'])

    # Pedidos pendientes (siempre todos, sin filtro de período)
    pedidos_pendientes = list(
        Pedido.objects.filter(estado=Pedido.PENDIENTE)
        .select_related('cliente')
        .prefetch_related('pagos')
    )
    total_por_cobrar = sum(p.balance_pendiente for p in pedidos_pendientes)

    # Tendencia últimos 6 meses
    trend_labels, trend_vendido, trend_ganancia_list, trend_gastos_list = [], [], [], []
    for i in range(5, -1, -1):
        tm = hoy.month - i
        ty = hoy.year
        while tm <= 0:
            tm += 12
            ty -= 1
        t_ini, t_fin = _mes_range(ty, tm)
        trend_labels.append(f"{_MESES_ES[tm-1]} {str(ty)[2:]}")
        agg = Pedido.objects.filter(
            estado=Pedido.PAGADO, fecha__gte=t_ini, fecha__lt=t_fin
        ).aggregate(v=Sum(_VENDIDO_EXPR), g=Sum(_GANANCIA_EXPR))
        g_val = Gasto.objects.filter(fecha__gte=t_ini, fecha__lt=t_fin).aggregate(t=Sum('monto'))['t'] or Decimal('0')
        trend_vendido.append(float(agg['v'] or 0))
        trend_ganancia_list.append(float(agg['g'] or 0))
        trend_gastos_list.append(float(g_val))

    # Meses disponibles para el selector (12 meses hacia atrás + opción "todo")
    meses_disponibles = []
    for i in range(11, -1, -1):
        tm = hoy.month - i
        ty = hoy.year
        while tm <= 0:
            tm += 12
            ty -= 1
        meses_disponibles.append({'valor': f"{ty}-{tm:02d}", 'label': f"{_MESES_ES[tm-1]} {ty}"})

    return render(request, 'negocio/resumen.html', {
        'total_vendido': total_vendido,
        'total_costo': total_costo,
        'total_cobrado': total_cobrado,
        'total_ganancia': total_ganancia,
        'total_gastos': total_gastos,
        'ganancia_neta': ganancia_neta,
        'margen_pct': margen_pct,
        'cobros_metodo': cobros_metodo,
        'gastos_cat': gastos_cat,
        'pedidos_pendientes': pedidos_pendientes,
        'total_por_cobrar': total_por_cobrar,
        'n_pendientes': len(pedidos_pendientes),
        'mes': mes,
        'periodo_label': periodo_label,
        'meses_disponibles': meses_disponibles,
        'trend_labels': json.dumps(trend_labels),
        'trend_vendido': json.dumps(trend_vendido),
        'trend_ganancia': json.dumps(trend_ganancia_list),
        'trend_gastos': json.dumps(trend_gastos_list),
    })


@staff_member_required
def pos(request):
    """Pantalla POS móvil. Pasa categorías raíz (filtro) y clientes (selector)."""
    categorias = Category.objects.filter(parent__isnull=True).order_by('name')
    clientes = Cliente.objects.order_by('nombre')
    return render(request, 'negocio/pos.html', {
        'categorias': categorias, 'clientes': clientes,
    })


# ── POS ───────────────────────────────────────────────────

PAGE_SIZE = 24  # cards por página


@staff_member_required
def pos_productos(request):
    """Grid de productos activos para el POS. Filtros: q (nombre/SKU), categoria (pk).
    Sin filtro = "más vendidos arriba" (agregado de PedidoItem). Paginado. JSON.
    La búsqueda usa el ORM (parametrizado → sin SQLi)."""
    qs = (
        Product.objects.filter(is_active=True)
        .select_related('category', 'category__parent')
        .prefetch_related('images')
    )

    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))

    categoria = (request.GET.get('categoria') or '').strip()
    if categoria.isdigit():
        qs = qs.filter(Q(category_id=int(categoria)) | Q(category__parent_id=int(categoria)))

    if not q and not categoria.isdigit():
        # Vista por defecto: más vendidos primero (vacío al inicio → cae a recientes)
        qs = qs.annotate(_vendidos=Count('ventas_items')).order_by(
            '-_vendidos', 'display_order', '-created_at')
    else:
        qs = qs.order_by('display_order', '-created_at')

    try:
        page_num = max(1, int(request.GET.get('page', 1)))
    except (TypeError, ValueError):
        page_num = 1
    page = Paginator(qs, PAGE_SIZE).get_page(page_num)

    signer = Signer(salt='negocio-label')
    productos = []
    for p in page.object_list:
        cover = next((img for img in p.images.all() if img.is_cover), None)
        if cover is None:
            cover = next(iter(p.images.all()), None)
        label_token = quote(signer.sign(p.sku), safe='')
        productos.append({
            'sku': p.sku,
            'nombre': p.name,
            'precio': str(p.final_price),
            'imagen_url': cover.image.url if cover and cover.image else '',
            'categoria_id': p.category_id,
            'categoria_raiz_id': p.category.parent_id or p.category_id,
            'categoria_nombre': (p.category.parent.name if p.category.parent_id
                                 else p.category.name),
            'label_bprint_url': (
                f"bprint://{request.scheme}://{request.get_host()}"
                f"/panel/negocio/api/label/{p.sku}/?token={label_token}"
            ),
            'label_usb_url': (
                f"/panel/negocio/label/{p.sku}/?token={label_token}&print=1"
            ),
        })

    return JsonResponse({'productos': productos, 'has_next': page.has_next()})


# ── POS: cobrar ───────────────────────────────────────────

@staff_member_required
@require_POST
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def pos_cobrar(request):
    """Crea una venta de tienda física. Payload JSON:
    {lineas:[{sku,cantidad,precio_unitario}], cliente_id?, metodo_pago}.
    staff-only + POST + CSRF + rate limit. Toda la validación de negocio en el servicio."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    lineas = payload.get('lineas')
    if not isinstance(lineas, list):
        return JsonResponse({'ok': False, 'error': 'lineas debe ser una lista'}, status=400)

    cliente = None
    cliente_id = payload.get('cliente_id')
    if cliente_id not in (None, '', 0):
        try:
            cliente = Cliente.objects.get(pk=cliente_id)
        except (Cliente.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'ok': False, 'error': 'Cliente no encontrado'}, status=400)

    metodo_pago = payload.get('metodo_pago', Pago.EFECTIVO)

    try:
        pedido = crear_venta_tienda(
            lineas=lineas, cliente=cliente, metodo_pago=metodo_pago,
        )
    except VentaInvalida as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    signer = Signer(salt='negocio-receipt')
    token = quote(signer.sign(str(pedido.pk)), safe='')
    bprint_url = (
        f"bprint://{request.scheme}://{request.get_host()}"
        f"/panel/negocio/api/receipt/{pedido.pk}/?token={token}"
    )

    # Refetch con prefetch para obtener ítems y pago sin N+1
    pedido_full = (
        Pedido.objects.prefetch_related('items', 'pagos')
        .get(pk=pedido.pk)
    )
    pago = next(iter(pedido_full.pagos.all()), None)
    metodo_display = pago.get_metodo_pago_display() if pago else 'Efectivo'
    lineas_display = [
        {
            'nombre': item.nombre_snapshot,
            'cantidad': item.cantidad,
            'precio_unitario': str(item.precio_unitario),
            'subtotal': str(item.subtotal),
        }
        for item in pedido_full.items.all()
    ]

    return JsonResponse({
        'ok': True,
        'pedido_id': pedido.pk,
        'total': str(pedido.precio_venta),
        'ganancia': str(pedido.ganancia),
        'bprint_url': bprint_url,
        'fecha': pedido_full.fecha.strftime('%d/%m/%Y'),
        'metodo_pago': metodo_display,
        'lineas': lineas_display,
    })


# ── Etiquetas — selección batch ───────────────────────────

@staff_member_required
def etiquetas_list(request):
    """Panel de impresión de etiquetas: selección por categoría / búsqueda."""
    cat = request.GET.get('cat', '').strip()
    q   = request.GET.get('q',   '').strip()

    qs = (Product.objects
          .filter(is_active=True)
          .select_related('category', 'category__parent')
          .prefetch_related('images')
          .order_by('category__name', 'name'))
    if cat:
        qs = qs.filter(Q(category__slug=cat) | Q(category__parent__slug=cat))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))

    signer   = Signer(salt='negocio-label')
    products = []
    for p in qs[:300]:
        cover = next((img for img in p.images.all() if img.is_cover), None) \
                or next(iter(p.images.all()), None)
        token = quote(signer.sign(p.sku), safe='')
        products.append({
            'sku':         p.sku,
            'name':        p.name,
            'final_price': p.final_price,
            'image_url':   cover.image.url if cover and cover.image else '',
            'category':    p.category.parent.name if p.category.parent_id else p.category.name,
            'bprint_url':  (f"bprint://{request.scheme}://{request.get_host()}"
                            f"/panel/negocio/api/label/{p.sku}/?token={token}"),
            'usb_url':     f"/panel/negocio/label/{p.sku}/?token={token}&print=1",
        })

    parent_cats = (Category.objects
                   .filter(parent=None, is_active=True)
                   .prefetch_related('subcategories')
                   .order_by('name'))

    return render(request, 'negocio/etiquetas.html', {
        'products':    products,
        'parent_cats': parent_cats,
        'cat_filter':  cat,
        'q':           q,
        'total':       qs.count(),
    })


from catalog.models import TipoArticulo, CodigoDescuento
from .forms import TipoArticuloForm, CodigoDescuentoForm


# ── TipoArticulo CRUD ────────────────────────────────────────────────────────

@staff_member_required
def tipos_list(request):
    tipos = TipoArticulo.objects.all()
    return render(request, 'negocio/tipo_articulo_list.html', {'tipos': tipos})


@staff_member_required
def tipo_create(request):
    form = TipoArticuloForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect('negocio:tipos_list')
    return render(request, 'negocio/tipo_articulo_form.html', {'form': form, 'titulo': 'Nuevo tipo de artículo'})


@staff_member_required
def tipo_edit(request, pk):
    tipo = get_object_or_404(TipoArticulo, pk=pk)
    form = TipoArticuloForm(request.POST or None, instance=tipo)
    if form.is_valid():
        form.save()
        return redirect('negocio:tipos_list')
    return render(request, 'negocio/tipo_articulo_form.html', {'form': form, 'titulo': f'Editar — {tipo.nombre}'})


@staff_member_required
def tipo_delete(request, pk):
    tipo = get_object_or_404(TipoArticulo, pk=pk)
    if request.method == 'POST':
        tipo.delete()
        return redirect('negocio:tipos_list')
    return render(request, 'negocio/tipo_articulo_form.html', {
        'form': None, 'titulo': f'Eliminar — {tipo.nombre}', 'objeto': tipo, 'confirm_delete': True,
    })


# ── CodigoDescuento CRUD ─────────────────────────────────────────────────────

@staff_member_required
def codigos_list(request):
    codigos = CodigoDescuento.objects.select_related('tipo_articulo', 'categoria_web').all()
    return render(request, 'negocio/codigo_descuento_list.html', {'codigos': codigos})


@staff_member_required
def codigo_create(request):
    form = CodigoDescuentoForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect('negocio:codigos_list')
    return render(request, 'negocio/codigo_descuento_form.html', {'form': form, 'titulo': 'Nuevo código'})


@staff_member_required
def codigo_edit(request, pk):
    codigo = get_object_or_404(CodigoDescuento, pk=pk)
    form = CodigoDescuentoForm(request.POST or None, instance=codigo)
    if form.is_valid():
        form.save()
        return redirect('negocio:codigos_list')
    return render(request, 'negocio/codigo_descuento_form.html', {'form': form, 'titulo': f'Editar — {codigo.codigo}'})


@staff_member_required
def codigo_delete(request, pk):
    codigo = get_object_or_404(CodigoDescuento, pk=pk)
    if request.method == 'POST':
        codigo.delete()
        return redirect('negocio:codigos_list')
    return render(request, 'negocio/codigo_descuento_form.html', {
        'form': None, 'titulo': f'Eliminar — {codigo.codigo}', 'objeto': codigo, 'confirm_delete': True,
    })


@staff_member_required
@require_POST
def etiquetas_print(request):
    """Renderiza página HTML multi-etiqueta para impresión USB batch."""
    skus = request.POST.getlist('skus')
    if not skus:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest('Sin SKUs')

    labels = []
    for p in (Product.objects
              .filter(sku__in=skus, is_active=True)
              .prefetch_related('images')):
        cover = next((img for img in p.images.all() if img.is_cover), None) \
                or next(iter(p.images.all()), None)
        labels.append({
            'sku':   p.sku,
            'name':  p.name,
            'price': p.final_price,
            'image_url': request.build_absolute_uri(cover.image.url) if cover and cover.image else '',
        })

    return render(request, 'negocio/label_html.html', {'labels': labels, 'autoprint': True})


# ── Etiqueta individual HTML (USB) ─────────────────────────

@ratelimit(key='header:X-Forwarded-For', rate='60/m', block=True)
def label_html(request, sku):
    """Renderiza una etiqueta HTML optimizada para 58mm — impresión USB."""
    token = request.GET.get('token', '')
    signer = Signer(salt='negocio-label')
    try:
        value = signer.unsign(token)
        if value != sku:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden()
    except BadSignature:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    product = get_object_or_404(
        Product.objects.prefetch_related('images'), sku=sku, is_active=True
    )
    cover = next((img for img in product.images.all() if img.is_cover), None) \
            or next(iter(product.images.all()), None)
    image_url = request.build_absolute_uri(cover.image.url) if cover and cover.image else ''

    return render(request, 'negocio/label_html.html', {
        'labels': [{'sku': product.sku, 'name': product.name,
                    'price': product.final_price, 'image_url': image_url}],
        'autoprint': request.GET.get('print') == '1',
    })


# ── Print endpoints (Bluetooth Print app) ─────────────────

@ratelimit(key='header:X-Forwarded-For', rate='60/m', block=True)
def receipt_print_json(request, pedido_id):
    """Devuelve JSON para Bluetooth Print app — ticket de venta.
    Público (sin sesión) pero protegido con token HMAC firmado por Django."""
    token = request.GET.get('token', '')
    signer = Signer(salt='negocio-receipt')
    try:
        value = signer.unsign(token)
        if value != str(pedido_id):
            return JsonResponse({'error': 'token inválido'}, status=403)
    except BadSignature:
        return JsonResponse({'error': 'token inválido'}, status=403)

    pedido = get_object_or_404(
        Pedido.objects.prefetch_related('items', 'pagos'), pk=pedido_id
    )
    return JsonResponse(_build_receipt_json(pedido))


@ratelimit(key='header:X-Forwarded-For', rate='60/m', block=True)
def label_print_json(request, sku):
    """Devuelve JSON para Bluetooth Print app — etiqueta de producto.
    Público (sin sesión) pero protegido con token HMAC firmado por Django."""
    token = request.GET.get('token', '')
    signer = Signer(salt='negocio-label')
    try:
        value = signer.unsign(token)
        if value != sku:
            return JsonResponse({'error': 'token inválido'}, status=403)
    except BadSignature:
        return JsonResponse({'error': 'token inválido'}, status=403)

    product = get_object_or_404(
        Product.objects.prefetch_related('images'), sku=sku, is_active=True
    )
    cover = next((img for img in product.images.all() if img.is_cover), None)
    if cover is None:
        cover = next(iter(product.images.all()), None)
    image_url = request.build_absolute_uri(cover.image.url) if cover and cover.image else None
    return JsonResponse(_build_label_json(product, image_url=image_url))
