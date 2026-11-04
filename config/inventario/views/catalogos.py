from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render

from catalog.models import TipoArticulo
from inventario.forms import (
    ColorForm, ColoresModeloForm, PrecioVentaForm, TallaForm, TarifaFleteForm)
from inventario.models import Color, PrecioVenta, TallaCalzado, TarifaFlete

# accion del POST -> (pestaña, mensaje de éxito)
_ACCIONES = {
    'color': ('colores', 'Color guardado.'),
    'colores_modelo': ('colores', 'Colores del modelo actualizados.'),
    'talla': ('tallas', 'Talla guardada.'),
    'tarifa': ('fletes', 'Tarifa actualizada.'),
    'precio': ('precios', 'Precio de venta guardado.'),
}


def _formulario(accion, post):
    if accion == 'color':
        return ColorForm(post)
    if accion == 'colores_modelo':
        return ColoresModeloForm(post)
    if accion == 'talla':
        return TallaForm(post)
    if accion == 'tarifa':
        tarifa = get_object_or_404(TarifaFlete, pk=int(post.get('tarifa_id', '')) if post.get('tarifa_id', '').isdigit() else 0)
        return TarifaFleteForm(post, instance=tarifa)
    if accion == 'precio':
        return PrecioVentaForm(post)
    return None


@staff_member_required
def catalogos(request):
    forms_vacios = {
        'color': ColorForm(), 'colores_modelo': ColoresModeloForm(),
        'talla': TallaForm(), 'precio': PrecioVentaForm(),
    }
    activa = request.GET.get('tab', 'colores')
    if request.method == 'POST':
        accion = request.POST.get('accion', '')
        form = _formulario(accion, request.POST)
        if form is None:
            messages.error(request, 'Acción desconocida.')
            return redirect('inventario:catalogos')
        if form.is_valid():
            form.save()
            tab, ok = _ACCIONES[accion]
            messages.success(request, ok)
            return redirect(f"{request.path}?tab={tab}")
        # Con errores se re-renderiza el formulario fallido en su pestaña.
        activa = _ACCIONES[accion][0]
        if accion in forms_vacios:
            forms_vacios[accion] = form
        else:
            messages.error(request, '; '.join(
                e for errs in form.errors.values() for e in errs))
    modelos = TipoArticulo.objects.filter(padre__isnull=True).prefetch_related(
        'colores', 'precios_venta').order_by('nombre')
    return render(request, 'inventario/catalogos.html', {
        'activa': activa,
        'tabs': [('colores', 'Colores'), ('tallas', 'Tallas'), ('fletes', 'Fletes'), ('precios', 'Precios de venta')],
        'forms': forms_vacios,
        'colores': Color.objects.all(),
        'tallas': TallaCalzado.objects.all(),
        'tarifas': [(t, TarifaFleteForm(instance=t)) for t in TarifaFlete.objects.order_by('via')],
        'modelos': modelos,
    })
