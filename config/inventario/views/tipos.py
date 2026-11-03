from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from catalog.models import TipoArticulo
from inventario.services.tipos import crear_variante

VIA = 'aereo'


def _convertibles():
    # Sin padre y sin variante aérea todavía: la pantalla se puede reusar.
    return (TipoArticulo.objects.filter(padre__isnull=True)
            .exclude(variantes__via=VIA).order_by('nombre'))


@staff_member_required
def tipos_convertir(request):
    if request.method == 'POST':
        ids = request.POST.getlist('tipo_ids')
        creadas, errores = 0, []
        for tipo in _convertibles().filter(pk__in=[i for i in ids if i.isdigit()]):
            try:
                crear_variante(tipo, VIA)
                creadas += 1
            except (ValidationError, ValueError) as e:
                detalle = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
                errores.append(f'{tipo.nombre}: {detalle}')
        if creadas:
            messages.success(request, f'{creadas} variante{"s" if creadas != 1 else ""} aérea{"s" if creadas != 1 else ""} creada{"s" if creadas != 1 else ""}.')
        for e in errores:
            messages.error(request, e)
        if not ids:
            messages.error(request, 'Selecciona al menos un tipo.')
        return redirect('inventario:tipos_convertir')
    return render(request, 'inventario/tipos_convertir.html', {'tipos': _convertibles()})
