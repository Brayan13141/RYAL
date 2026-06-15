import json
from decimal import Decimal
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST

from django_ratelimit.decorators import ratelimit

from catalog.models import Category, Product
from .forms import ClienteForm, PedidoForm, PagoForm, GastoForm
from .models import Cliente, Pedido, Pago, Gasto
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
    return render(request, 'negocio/pedidos.html', {'pedidos': pedidos})


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
        Pedido.objects.prefetch_related('pagos'), pk=pk
    )
    pago_form = PagoForm()
    return render(request, 'negocio/pedido_detail.html', {
        'pedido': pedido, 'pago_form': pago_form
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
        if pedido.balance_pendiente <= 0:
            pedido.estado = Pedido.PAGADO
            pedido.save()
        return JsonResponse({
            'ok': True,
            'balance_pendiente': str(pedido.balance_pendiente),
            'estado': pedido.estado,
            'estado_display': pedido.get_estado_display(),
        })
    return JsonResponse({'ok': False, 'errors': form.errors}, status=400)


# ── Gastos ────────────────────────────────────────────────

@staff_member_required
def gastos_list(request):
    gastos = Gasto.objects.all()
    form = GastoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('negocio:gastos_list')
    return render(request, 'negocio/gastos.html', {'gastos': gastos, 'form': form})


# ── Resumen ───────────────────────────────────────────────

@staff_member_required
def resumen(request):
    pedidos_pagados = list(
        Pedido.objects.filter(estado=Pedido.PAGADO).prefetch_related('pagos')
    )
    total_vendido = sum(p.precio_venta for p in pedidos_pagados)
    total_ganancia = sum(p.ganancia for p in pedidos_pagados)
    total_gastos = Gasto.objects.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    ganancia_neta = total_ganancia - total_gastos

    pedidos_pendientes = list(
        Pedido.objects.filter(estado=Pedido.PENDIENTE).prefetch_related('pagos')
    )
    total_por_cobrar = sum(p.balance_pendiente for p in pedidos_pendientes)

    return render(request, 'negocio/resumen.html', {
        'total_vendido': total_vendido,
        'total_ganancia': total_ganancia,
        'total_gastos': total_gastos,
        'ganancia_neta': ganancia_neta,
        'total_por_cobrar': total_por_cobrar,
        'n_pendientes': len(pedidos_pendientes),
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

    productos = []
    for p in page.object_list:
        cover = next((img for img in p.images.all() if img.is_cover), None)
        if cover is None:
            cover = next(iter(p.images.all()), None)
        productos.append({
            'sku': p.sku,
            'nombre': p.name,
            'precio': str(p.final_price),
            'imagen_url': cover.image.url if cover and cover.image else '',
            'categoria_id': p.category_id,
            'categoria_raiz_id': p.category.parent_id or p.category_id,
            'categoria_nombre': (p.category.parent.name if p.category.parent_id
                                 else p.category.name),
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

    return JsonResponse({
        'ok': True,
        'pedido_id': pedido.pk,
        'total': str(pedido.precio_venta),
        'ganancia': str(pedido.ganancia),
    })
