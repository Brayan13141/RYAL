from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from catalog.models import TipoArticulo
from inventario.models import VIAS
from inventario.services.tipos import (
    costo_sugerido, crear_variante, proveedor_sugerido, tarifas_decimal)

_CENTAVOS = Decimal('0.01')


def _filas(tarifas):
    """Un renglón por modelo base con el proveedor sugerido y
    [(via, variante|None, costo sugerido)] en el orden de VIAS."""
    bases = (TipoArticulo.objects.filter(padre__isnull=True)
             .prefetch_related('variantes').order_by('nombre'))
    filas = []
    for base in bases:
        por_via = {v.via: v for v in base.variantes.all()}
        proveedor = proveedor_sugerido(base, tarifas)
        filas.append({
            'base': base, 'proveedor': proveedor, 'con_variantes': bool(por_via),
            'celdas': [(via, por_via.get(via), costo_sugerido(base, via, tarifas, proveedor))
                       for via in VIAS],
        })
    return filas


def _leer_monto(texto, que):
    """Decimal >= 0 con centavos; None si viene vacío."""
    texto = (texto or '').strip().replace(',', '.')
    if not texto:
        return None
    try:
        monto = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f'{que} "{texto}" no es un número')
    if not monto.is_finite() or monto < 0:
        raise ValueError(f'{que} "{texto}" no es válido')
    return monto.quantize(_CENTAVOS)


def _costo_de(post, base, via, tarifas):
    """Costo capturado en la celda; si viene vacío, proveedor (capturado o
    sugerido) + flete de la vía."""
    costo = _leer_monto(post.get(f'costo-{base.pk}-{via}'), 'el costo')
    if costo is not None:
        return costo
    proveedor = _leer_monto(post.get(f'proveedor-{base.pk}'), 'el precio del proveedor')
    return costo_sugerido(base, via, tarifas, proveedor)


@staff_member_required
def tipos_convertir(request):
    tarifas = tarifas_decimal()
    if request.method == 'POST':
        pedidos = request.POST.getlist('crear')
        bases = {b.pk: b for b in TipoArticulo.objects.filter(padre__isnull=True)}
        creadas, errores = 0, []
        for valor in pedidos:
            pk, _, via = valor.partition(':')
            base = bases.get(int(pk)) if pk.isdigit() else None
            if base is None or via not in VIAS:
                continue
            try:
                crear_variante(base, via, costo=_costo_de(request.POST, base, via, tarifas))
                creadas += 1
            except (ValidationError, ValueError) as e:
                detalle = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
                errores.append(f'{base.nombre} ({VIAS[via].lower()}): {detalle}')
        if creadas:
            s = 's' if creadas != 1 else ''
            messages.success(request, f'{creadas} variante{s} creada{s}.')
        for e in errores:
            messages.error(request, e)
        if not pedidos:
            messages.error(request, 'Marca al menos una vía para crear.')
        return redirect('inventario:tipos_convertir')
    filas = _filas(tarifas)
    return render(request, 'inventario/tipos_convertir.html', {
        'filas': filas, 'vias': VIAS,
        'tarifas': {via: format(m.normalize(), 'f') for via, m in tarifas.items()},
        'n_con_variantes': sum(f['con_variantes'] for f in filas)})
