from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from catalog.models import TipoArticulo
from inventario.models import TallaCalzado, fmt_talla
from inventario.services.stock import alertas, matriz_stock

VIAS = ('aereo', 'maritimo')
ESTADOS = (('por_recibir', 'Por recibir'), ('en_stock', 'En stock'),
           ('vendido', 'Vendido'), ('baja', 'Baja'))


def _entero_o_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _tablas(matriz):
    """Prepara cada base para la plantilla: columnas de talla ordenadas y celdas alineadas."""
    claves = {t for m in matriz for f in m['colores'] for t in f['tallas']}
    orden = {t.mx_cm: t.orden for t in TallaCalzado.objects.filter(mx_cm__in=claves)}
    columnas = sorted(claves, key=lambda t: (orden.get(t, 0), t))
    etiquetas = [fmt_talla(t) for t in columnas]
    vacia = {'aereo': 0, 'maritimo': 0}
    tablas = []
    for m in matriz:
        filas = [{
            'color': f['color'], 'total': f['total'],
            'celdas': [f['tallas'].get(t, vacia) for t in columnas]}
            for f in m['colores']]
        totales = [{'aereo': sum(f['tallas'].get(t, vacia)['aereo'] for f in m['colores']),
                    'maritimo': sum(f['tallas'].get(t, vacia)['maritimo'] for f in m['colores'])}
                   for t in columnas]
        tablas.append({'base': m['base'], 'total': m['total'], 'filas': filas,
                       'totales': totales, 'columnas': etiquetas})
    return tablas


@staff_member_required
def stock(request):
    base_id = _entero_o_none(request.GET.get('base_id'))
    via = request.GET.get('via')
    if via not in VIAS:
        via = None
    estado = request.GET.get('estado')
    if estado not in dict(ESTADOS):
        estado = 'en_stock'
    bases = TipoArticulo.objects.filter(padre__isnull=True, variantes__isnull=False).distinct()
    if base_id is not None and not bases.filter(pk=base_id).exists():
        base_id = None
    return render(request, 'inventario/stock.html', {
        'tablas': _tablas(matriz_stock(base_id=base_id, via=via, estado=estado)),
        'alertas': alertas(),
        'bases': bases, 'base_id': base_id, 'via': via,
        'estado': estado, 'estados': ESTADOS, 'estado_label': dict(ESTADOS)[estado].lower(),
    })
