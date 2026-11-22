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
    vias = {'a': por_via.get('aereo', 0), 'm': por_via.get('maritimo', 0),
            'nac': por_via.get('nacional', 0)}
    return {'n': sum(n for _, n in con), 'desglose': desglose, 'vias': vias}


def _clave_color(color):
    """El mismo color en modelos distintos es un solo chip: se agrupa por nombre."""
    return color.nombre.strip().lower()


def _tablas(matriz):
    """Una card por modelo base: solo las tallas que ese modelo tiene, celdas alineadas.

    Devuelve también los chips de talla (todas las tallas presentes, con su total) en el
    orden de `TallaCalzado.orden`, y los de color (por nombre, con su total). Cada celda
    lleva su `clave` de talla y cada fila la de su color, para que el navegador filtre
    sin recargar.
    """
    claves = {t for m in matriz for f in m['colores'] for t in f['tallas']}
    orden = {t.mx_cm: t.orden for t in TallaCalzado.objects.filter(mx_cm__in=claves)}

    def ordenar(ts):
        return sorted(ts, key=lambda t: (orden.get(t, 0), t))

    vacia = dict.fromkeys(VIAS, 0)
    por_talla = {}
    por_color = {}  # clave -> {'clave', 'nombre', 'n'}
    tablas = []
    for m in matriz:
        columnas = ordenar({t for f in m['colores'] for t in f['tallas']})
        filas = []
        for f in m['colores']:
            celdas = []
            for t in columnas:
                celda = _celda(f['tallas'].get(t, vacia))
                celda['clave'] = fmt_talla(t)
                celdas.append(celda)
            clave = _clave_color(f['color'])
            chip = por_color.setdefault(clave, {
                'clave': clave, 'nombre': f['color'].nombre.strip().capitalize(), 'n': 0})
            chip['n'] += f['total']
            filas.append({'color': f['color'], 'clave_color': clave, 'total': f['total'],
                          'celdas': celdas})
        totales = []
        for t in columnas:
            n = sum(sum(f['tallas'].get(t, vacia).values()) for f in m['colores'])
            por_talla[t] = por_talla.get(t, 0) + n
            totales.append({'clave': fmt_talla(t), 'n': n})
        tablas.append({'base': m['base'], 'total': m['total'], 'filas': filas,
                       'totales': totales, 'columnas': [fmt_talla(t) for t in columnas]})
    tallas = [{'clave': fmt_talla(t), 'n': por_talla[t]} for t in ordenar(por_talla)]
    colores = [por_color[k] for k in sorted(por_color)]
    return tablas, tallas, colores


def _chips_via(via):
    """Chips de vía: los pinta el navegador; `via` (de la URL) solo los deja premarcados."""
    opciones = [(None, 'Todas')] + list(VIAS.items())
    return [{'valor': valor, 'etiqueta': nombre, 'on': valor == via} for valor, nombre in opciones]


def _pestanas(base_id):
    """Una pestaña por estado con su conteo (con el mismo filtro de modelo; la vía no
    cuenta porque se filtra en el navegador) y el query string que conserva ese filtro:
    así lo dado de alta y aún sin recibir se ve."""
    qs = Par.objects.all()
    if base_id is not None:
        qs = qs.filter(tipo__padre_id=base_id)
    n = dict(qs.values_list('estado').annotate(c=Count('pk')))
    filtros = {'base_id': base_id} if base_id is not None else {}
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
    tablas, tallas, colores = _tablas(matriz_stock(base_id=base_id, via=None, estado=estado))
    return render(request, 'inventario/stock.html', {
        'pestanas': _pestanas(base_id),
        'tablas': tablas, 'tallas': tallas, 'colores': colores,
        'chips_via': _chips_via(via),
        'alertas': alertas(),
        'bases': bases, 'base_id': base_id,
        'estado': estado, 'estados': ESTADOS, 'estado_label': dict(ESTADOS)[estado].lower(),
    })
