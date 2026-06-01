from decimal import Decimal
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .forms import ClienteForm, PedidoForm, PagoForm, GastoForm
from .models import Cliente, Pedido, Pago, Gasto


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
