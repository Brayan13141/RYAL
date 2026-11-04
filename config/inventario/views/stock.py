from urllib.parse import urlencode

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.shortcuts import render

from catalog.models import TipoArticulo
from inventario.models import VIAS, Par, TallaCalzado, fmt_talla
from inventario.services.stock import alertas, matriz_stock
from inventario.services.tipos import LETRA_VIA


ESTADOS = (('por_recibir', 'Por recibir'), ('en_stock', 'En stock'),
           ('vendido', 'Vendido'), ('baja', 'Baja'))


def _entero_o_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _celda(por_via):
    """Total de la celda y su desglose: 'A' si es de una sola vía, 'A2 N1' si mezcla."""
    con = [(LETRA_VIA[v], n) for v, n in por_via.items() if n]
    if len(con) == 1:
        desglose = con[0][0]
    else:
        desglose = ' '.join(f'{letra}{n}' for letra, n in con)
    return {'n': sum(n for _, n in con), 'desglose': desglose}


def _tablas(matriz):
    """Prepara cada base para la plantilla: columnas de talla ordenadas y celdas alineadas."""
    claves = {t for m in matriz for f in m['colores'] for t in f['tallas']}
    orden = {t.mx_cm: t.orden for t in TallaCalzado.objects.filter(mx_cm__in=claves)}
    columnas = sorted(claves, key=lambda t: (orden.get(t, 0), t))
    etiquetas = [fmt_talla(t) for t in columnas]
    vacia = dict.fromkeys(VIAS, 0)
    tablas = []
    for m in matriz:
        filas = [{
            'color': f['color'], 'total': f['total'],
            'celdas': [_celda(f['tallas'].get(t, vacia)) for t in columnas]}
            for f in m['colores']]
        totales = [sum(sum(f['tallas'].get(t, vacia).values()) for f in m['colores'])
                   for t in columnas]
        tablas.append({'base': m['base'], 'total': m['total'], 'filas': filas,
                       'totales': totales, 'columnas': etiquetas})
    return tablas


def _pestanas(base_id, via):
    """Una pestaña por estado con su conteo (con los mismos filtros de modelo y vía) y el
    query string que conserva esos filtros: así lo dado de alta y aún sin recibir se ve."""
    qs = Par.objects.all()
    if base_id is not None:
        qs = qs.filter(tipo__padre_id=base_id)
    if via is not None:
        qs = qs.filter(tipo__via=via)
    n = dict(qs.values_list('estado').annotate(c=Count('pk')))
    filtros = {k: v for k, v in (('base_id', base_id), ('via', via)) if v is not None}
    return [{'valor': valor, 'etiqueta': etiqueta, 'n': n.get(valor, 0),
             'query': urlencode({'estado': valor, **filtros})}
            for valor, etiqueta in ESTADOS]


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
        'pestanas': _pestanas(base_id, via),
        'tablas': _tablas(matriz_stock(base_id=base_id, via=via, estado=estado)),
        'alertas': alertas(),
        'bases': bases, 'base_id': base_id, 'via': via, 'vias': VIAS,
        'estado': estado, 'estados': ESTADOS, 'estado_label': dict(ESTADOS)[estado].lower(),
    })
