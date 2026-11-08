from urllib.parse import urlencode

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.shortcuts import render

from catalog.models import TipoArticulo
from inventario.models import VIAS, Par, TallaCalzado, fmt_talla
from inventario.services.cantidad import stock_actual
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
    """Una card por modelo base: solo las tallas que ese modelo tiene, celdas alineadas.

    Devuelve también los chips de talla (todas las tallas presentes, con su total) en el
    orden de `TallaCalzado.orden`. Cada celda lleva su `clave` de talla para que el
    navegador pueda filtrar columnas sin recargar.
    """
    claves = {t for m in matriz for f in m['colores'] for t in f['tallas']}
    orden = {t.mx_cm: t.orden for t in TallaCalzado.objects.filter(mx_cm__in=claves)}

    def ordenar(ts):
        return sorted(ts, key=lambda t: (orden.get(t, 0), t))

    vacia = dict.fromkeys(VIAS, 0)
    por_talla = {}
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
            filas.append({'color': f['color'], 'total': f['total'], 'celdas': celdas})
        totales = []
        for t in columnas:
            n = sum(sum(f['tallas'].get(t, vacia).values()) for f in m['colores'])
            por_talla[t] = por_talla.get(t, 0) + n
            totales.append({'clave': fmt_talla(t), 'n': n})
        tablas.append({'base': m['base'], 'total': m['total'], 'filas': filas,
                       'totales': totales, 'columnas': [fmt_talla(t) for t in columnas]})
    tallas = [{'clave': fmt_talla(t), 'n': por_talla[t]} for t in ordenar(por_talla)]
    return tablas, tallas


def _chips_via(estado, via):
    """Chips de vía como enlaces: cambiar de vía conserva el estado elegido."""
    opciones = [(None, 'Todas')] + list(VIAS.items())
    return [{'etiqueta': nombre, 'on': valor == via,
             'query': urlencode({'estado': estado, **({'via': valor} if valor else {})})}
            for valor, nombre in opciones]


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
    cantidades = [{'tipo': t, 'stock': stock_actual(t)}
                  for t in TipoArticulo.objects.filter(stock_por_cantidad=True).order_by('nombre')]
    activables = (TipoArticulo.objects
                  .filter(padre__isnull=True, variantes__isnull=True, usa_tallas=False,
                          stock_por_cantidad=False)
                  .exclude(pk__in=Par.objects.values('tipo'))
                  .order_by('nombre'))
    tablas, tallas = _tablas(matriz_stock(base_id=base_id, via=via, estado=estado))
    return render(request, 'inventario/stock.html', {
        'pestanas': _pestanas(base_id, via),
        'tablas': tablas, 'tallas': tallas, 'chips_via': _chips_via(estado, via),
        'alertas': alertas(), 'cantidades': cantidades, 'activables': activables,
        'bases': bases, 'base_id': base_id, 'via': via, 'vias': VIAS,
        'estado': estado, 'estados': ESTADOS, 'estado_label': dict(ESTADOS)[estado].lower(),
    })
