"""Datos de las descargas del Stock (PDF para clientes y Excel/PDF interno).

Servicio puro: devuelve estructuras de Python; los generadores las dibujan.
Siempre `en_stock` (más `por_recibir` como "en camino" en el interno), sin importar
la pestaña activa del Stock.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Sum

from catalog.models import TipoArticulo
from inventario.models import (
    CALIDAD_DE_VIA, VIAS, Color, Par, PrecioLista, PrecioVenta, fmt_talla, foto_de)
from inventario.services.cantidad import stock_actual

CENTAVOS = Decimal('0.01')


class FiltroInvalido(ValueError):
    pass


@dataclass(frozen=True)
class Filtro:
    """Un conjunto vacío significa sin filtro en esa dimensión."""
    modelos: frozenset
    tallas: frozenset
    colores: frozenset
    vias: frozenset


def filtro_de_query(querydict):
    modelos = set()
    for valor in querydict.getlist('modelo'):
        valor = valor.strip()
        if not valor:
            continue
        try:
            modelos.add(int(valor))
        except ValueError:
            raise FiltroInvalido(f'Modelo inválido: {valor!r}') from None
    vias = {v.strip() for v in querydict.getlist('via') if v.strip()}
    for via in vias:
        if via not in VIAS:
            raise FiltroInvalido(f'Vía inválida: {via!r}')
    tallas = {t.strip() for t in querydict.getlist('talla') if t.strip()}
    colores = {c.strip().lower() for c in querydict.getlist('color') if c.strip()}
    return Filtro(frozenset(modelos), frozenset(tallas), frozenset(colores), frozenset(vias))


def _agrupado(filtro, estado):
    """Pares del `estado` agregados en la BD por base·color·talla·vía, ya filtrados."""
    qs = Par.objects.filter(estado=estado, tipo__padre__isnull=False)
    if filtro.modelos:
        qs = qs.filter(tipo__padre_id__in=filtro.modelos)
    if filtro.vias:
        qs = qs.filter(tipo__via__in=filtro.vias)
    filas = (qs.values('tipo__padre_id', 'color_id', 'talla__mx_cm', 'talla__orden', 'tipo__via')
             .annotate(n=Count('pk'), costo_sum=Sum('costo')))
    return [f for f in filas if _pasa(filtro, f)]


def _pasa(filtro, fila):
    # El color se filtra por nombre en la fila resuelta (ver `_contexto`); aquí solo la talla.
    return not filtro.tallas or fmt_talla(fila['talla__mx_cm']) in filtro.tallas


def _contexto(filas_por_estado, filtro):
    """Bases y colores de las filas, con el filtro de color aplicado (por nombre, como `_clave_color`)."""
    todas = [f for filas in filas_por_estado for f in filas]
    bases = {b.pk: b for b in TipoArticulo.objects.filter(pk__in={f['tipo__padre_id'] for f in todas})}
    colores = {c.pk: c for c in Color.objects.filter(pk__in={f['color_id'] for f in todas})}
    if filtro.colores:
        colores = {pk: c for pk, c in colores.items() if c.nombre.strip().lower() in filtro.colores}
    return bases, colores


def datos_clientes(filtro):
    filas = _agrupado(filtro, Par.Estado.EN_STOCK)
    bases, colores = _contexto([filas], filtro)
    grupos = {}  # (base_id, color_id) -> {talla orden/mx: str}
    for f in filas:
        if f['color_id'] not in colores:
            continue
        tallas = grupos.setdefault((f['tipo__padre_id'], f['color_id']), {})
        tallas[(f['talla__orden'], f['talla__mx_cm'])] = fmt_talla(f['talla__mx_cm'])
    resultado = []
    for (base_id, color_id), tallas in grupos.items():
        base, color = bases[base_id], colores[color_id]
        resultado.append({
            'base': base, 'color': color.nombre, 'foto': foto_de(base, color),
            'tallas': [tallas[k] for k in sorted(tallas)]})
    resultado.sort(key=lambda g: (g['base'].nombre, g['color']))
    return resultado


def _promedio(suma, n):
    return (suma / n).quantize(CENTAVOS) if n else None


def datos_internos(filtro):
    en_stock = _agrupado(filtro, Par.Estado.EN_STOCK)
    por_recibir = _agrupado(filtro, Par.Estado.POR_RECIBIR)
    bases, colores = _contexto([en_stock, por_recibir], filtro)

    celdas = {}  # (base_id, color_id, orden, mx_cm, calidad) -> fila

    def celda(f):
        calidad = CALIDAD_DE_VIA[f['tipo__via']]
        clave = (f['tipo__padre_id'], f['color_id'], f['talla__orden'], f['talla__mx_cm'], calidad)
        return celdas.setdefault(clave, {
            'talla': fmt_talla(f['talla__mx_cm']), 'calidad': calidad,
            'por_via': dict.fromkeys(VIAS, 0), 'total': 0, 'en_camino': 0, 'suma': Decimal('0')})

    for f in en_stock:
        if f['color_id'] not in colores:
            continue
        c = celda(f)
        c['por_via'][f['tipo__via']] += f['n']
        c['total'] += f['n']
        c['suma'] += f['costo_sum'] or Decimal('0')
    for f in por_recibir:
        if f['color_id'] in colores:
            celda(f)['en_camino'] += f['n']

    lista = {}
    for p in PrecioLista.objects.all():
        lista[(p.calidad, p.tipo_venta)] = p.precio
    precios_venta = {}
    for pv in PrecioVenta.objects.filter(tipo_id__in=bases):
        precios_venta.setdefault(pv.tipo_id, {})[pv.nombre] = pv.precio

    filas = []
    fotos = {}
    for (base_id, color_id, orden, _mx, calidad), c in sorted(
            celdas.items(),
            key=lambda kv: (bases[kv[0][0]].nombre, colores[kv[0][1]].nombre, kv[0][2], kv[0][4])):
        if c['total'] <= 0 and c['en_camino'] <= 0:
            continue
        base, color = bases[base_id], colores[color_id]
        if (base_id, color_id) not in fotos:
            fotos[(base_id, color_id)] = foto_de(base, color)
        filas.append({
            'base': base, 'color': color.nombre, 'foto': fotos[(base_id, color_id)],
            'talla': c['talla'], 'calidad': calidad, 'por_via': c['por_via'],
            'total': c['total'], 'en_camino': c['en_camino'],
            'costo_promedio': _promedio(c['suma'], c['total']), 'valor_costo': c['suma'],
            'mayoreo': lista.get((calidad, 'mayoreo')), 'menudeo': lista.get((calidad, 'menudeo')),
            'precios_venta': dict(precios_venta.get(base_id, {}))})

    por_cantidad = []
    for tipo in TipoArticulo.objects.filter(stock_por_cantidad=True).order_by('nombre'):
        existencias = stock_actual(tipo)
        por_cantidad.append({
            'tipo': tipo, 'cantidad': existencias, 'costo': tipo.costo,
            'valor': existencias * tipo.costo})

    return {
        'filas': filas,
        'nombres_precio_venta': sorted({n for f in filas for n in f['precios_venta']}),
        'por_cantidad': por_cantidad,
        'totales': {
            'total': sum(f['total'] for f in filas),
            'en_camino': sum(f['en_camino'] for f in filas),
            'valor_costo': sum((f['valor_costo'] for f in filas), Decimal('0'))},
    }
