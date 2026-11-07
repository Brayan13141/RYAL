from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from catalog.models import TipoArticulo
from negocio.models import Pedido
from inventario.forms import (
    ColorForm, ColoresModeloForm, PrecioListaForm, PrecioListaNuevaForm, PrecioVentaForm,
    TallaForm, TarifaFleteForm, TarifaNuevaForm)
from inventario.models import CALIDAD_CHOICES, Color, Par, PrecioLista, PrecioVenta, TallaCalzado, TarifaFlete

TABS = [('colores', 'Colores'), ('tallas', 'Tallas'), ('fletes', 'Fletes'),
        ('precios', 'Precios de venta')]

# accion del POST -> (pestaña, mensaje de éxito)
_ACCIONES = {
    'color': ('colores', 'Color guardado.'),
    'colores_modelo': ('colores', 'Colores del modelo actualizados.'),
    'talla': ('tallas', 'Talla guardada.'),
    'tarifa': ('fletes', 'Tarifa actualizada.'),
    'tarifa_nueva': ('fletes', 'Tarifa dada de alta.'),
    'precio': ('precios', 'Precio de venta guardado.'),
    'precio_lista': ('precios', 'Lista de precios actualizada.'),
    'precio_lista_nueva': ('precios', 'Precio de lista dado de alta.'),
}

# Registros que se editan en el formulario de la izquierda: accion -> (modelo, form, pestaña)
_EDITABLES = {
    'color': (Color, ColorForm, 'colores'),
    'talla': (TallaCalzado, TallaForm, 'tallas'),
    'precio': (PrecioVenta, PrecioVentaForm, 'precios'),
}

# accion_borrar -> (modelo, pestaña, qué se borró)
_BORRABLES = {
    'color_borrar': (Color, 'colores', 'Color borrado.'),
    'talla_borrar': (TallaCalzado, 'tallas', 'Talla borrada.'),
    'tarifa_borrar': (TarifaFlete, 'fletes', 'Tarifa borrada.'),
    'precio_borrar': (PrecioVenta, 'precios', 'Precio borrado.'),
    'precio_lista_borrar': (PrecioLista, 'precios', 'Precio de lista borrado.'),
}

# accion_estado -> (modelo, campo booleano, pestaña)
_ESTADOS = {
    'color_estado': (Color, 'activo', 'colores'),
    'talla_estado': (TallaCalzado, 'activa', 'tallas'),
}


def _pk(valor):
    """pk de un parámetro; cualquier cosa que no sea un entero positivo es 404."""
    valor = (valor or '').strip()
    if not valor.isdigit():
        raise Http404
    return int(valor)


def _instancia(modelo, post):
    """La fila a editar si el POST trae pk; None si es alta."""
    if 'pk' not in post or post.get('pk') == '':
        return None
    return get_object_or_404(modelo, pk=_pk(post.get('pk')))


def _formulario(accion, post):
    if accion in _EDITABLES:
        modelo, form_cls, _ = _EDITABLES[accion]
        return form_cls(post, instance=_instancia(modelo, post))
    if accion == 'colores_modelo':
        return ColoresModeloForm(post)
    if accion == 'tarifa':
        return TarifaFleteForm(post, instance=get_object_or_404(TarifaFlete, pk=_pk(post.get('tarifa_id'))))
    if accion == 'tarifa_nueva':
        return TarifaNuevaForm(post)
    if accion == 'precio_lista':
        fila = get_object_or_404(PrecioLista, pk=_pk(post.get('precio_lista_id')))
        return PrecioListaForm(post, instance=fila)
    if accion == 'precio_lista_nueva':
        return PrecioListaNuevaForm(post)
    return None


def _plural(n, uno, varios):
    return f'{n} {uno if n == 1 else varios}'


def _borrar(request, accion):
    modelo, tab, ok = _BORRABLES[accion]
    obj = get_object_or_404(modelo, pk=_pk(request.POST.get('pk')))
    try:
        obj.delete()
    except ProtectedError as e:
        n = len(e.protected_objects)
        messages.error(request, f'No se puede borrar «{obj}»: lo usan {_plural(n, "par", "pares")} '
                                'del inventario. Desactívalo para que ya no salga en altas.')
    else:
        messages.success(request, ok)
    return tab


def _cambiar_estado(request, accion):
    modelo, campo, tab = _ESTADOS[accion]
    obj = get_object_or_404(modelo, pk=_pk(request.POST.get('pk')))
    setattr(obj, campo, not getattr(obj, campo))
    obj.save(update_fields=[campo])
    estado = 'activo' if getattr(obj, campo) else 'inactivo'
    if modelo is TallaCalzado:
        estado = estado[:-1] + 'a'
    messages.success(request, f'«{obj}» quedó {estado}.')
    return tab


def _uso_por(campo):
    """{pk: pares que lo usan} para avisar antes de borrar."""
    return dict(Par.objects.order_by().values_list(campo).annotate(n=Count('pk')))


@staff_member_required
def catalogos(request):
    activa = request.GET.get('tab', 'colores')
    if activa not in dict(TABS):
        activa = 'colores'
    forms_vacios = {
        'color': ColorForm(), 'colores_modelo': ColoresModeloForm(),
        'talla': TallaForm(), 'precio': PrecioVentaForm(),
        'tarifa_nueva': TarifaNuevaForm(), 'precio_lista_nueva': PrecioListaNuevaForm(),
    }
    editando = None  # la fila que está abierta en el formulario de la izquierda

    if request.method == 'POST':
        accion = request.POST.get('accion', '')
        if accion in _BORRABLES:
            return redirect(f'{request.path}?tab={_borrar(request, accion)}')
        if accion in _ESTADOS:
            return redirect(f'{request.path}?tab={_cambiar_estado(request, accion)}')
        form = _formulario(accion, request.POST)
        if form is None:
            messages.error(request, 'Acción desconocida.')
            return redirect('inventario:catalogos')
        if form.is_valid():
            form.save()
            tab, ok = _ACCIONES[accion]
            messages.success(request, ok)
            return redirect(f'{request.path}?tab={tab}')
        # Con errores se re-renderiza el formulario fallido en su pestaña.
        activa = _ACCIONES[accion][0]
        if accion in forms_vacios:
            forms_vacios[accion] = form
            if accion in _EDITABLES and form.instance.pk:
                editando = form.instance
        else:
            messages.error(request, '; '.join(
                e for errs in form.errors.values() for e in errs))
    else:
        if request.GET.get('editar'):
            accion = next((a for a, (_, _, t) in _EDITABLES.items() if t == activa), None)
            if accion is None:
                raise Http404
            modelo, form_cls, _ = _EDITABLES[accion]
            editando = get_object_or_404(modelo, pk=_pk(request.GET.get('editar')))
            forms_vacios[accion] = form_cls(instance=editando)
        if activa == 'colores' and request.GET.get('modelo'):
            tipo = get_object_or_404(TipoArticulo, pk=_pk(request.GET.get('modelo')),
                                     padre__isnull=True)
            forms_vacios['colores_modelo'] = ColoresModeloForm(initial={
                'tipo': tipo.pk, 'colores': [c.pk for c in tipo.colores.all()]})

    modelos = TipoArticulo.objects.filter(padre__isnull=True).prefetch_related(
        'colores', 'precios_venta').order_by('nombre')
    uso_color, uso_talla = _uso_por('color'), _uso_por('talla')
    colores = list(Color.objects.annotate(n_modelos=Count('tipoarticulo')))
    for c in colores:
        c.n_pares = uso_color.get(c.pk, 0)
    tallas = list(TallaCalzado.objects.all())
    for t in tallas:
        t.n_pares = uso_talla.get(t.pk, 0)
    return render(request, 'inventario/catalogos.html', {
        'activa': activa,
        'tabs': TABS,
        'forms': forms_vacios,
        'editando': editando,
        'colores': colores,
        'tallas': tallas,
        'tarifas': [(t, TarifaFleteForm(instance=t)) for t in TarifaFlete.objects.order_by('via')],
        'hay_vias_libres': bool(forms_vacios['tarifa_nueva'].fields['via'].choices),
        'modelos': modelos,
        'modelos_con_precio': [t for t in modelos if t.precios_venta.all()],
        'lista': [(p, PrecioListaForm(instance=p)) for p in PrecioLista.objects.all()],
        'hay_lista_libre': PrecioLista.objects.count() < len(CALIDAD_CHOICES) * len(
            Pedido.TIPO_VENTA_CHOICES),
    })
