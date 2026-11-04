"""Matriz de stock (modelo base -> color x talla) y alertas de descuadre."""
from datetime import timedelta

from django.db.models import Count, Exists, F, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from catalog.models import TipoArticulo
from inventario.models import VIAS, Color, Lote, MovimientoPar, Par
from negocio.models import Pedido, PedidoItem
from negocio.services import cargar_aliases, resolver_tipo

# Un lote abierto con pares por recibir se vuelve alerta después de estos días.
DIAS_LOTE_CON_FALTANTES = 7

SKU_BOT = 'TIENDA-BOT'


def matriz_stock(*, base_id=None, via=None, estado=Par.Estado.EN_STOCK):
    """Pares del `estado` pedido (por defecto `en_stock`) agrupados por modelo base -> color -> talla, con desglose por vía.

    Se agrega en la BD (un GROUP BY), no par por par.
    """
    qs = Par.objects.filter(estado=estado)
    if base_id is not None:
        qs = qs.filter(tipo__padre_id=base_id)
    if via is not None:
        qs = qs.filter(tipo__via=via)
    filas = (qs.values('tipo__padre_id', 'color_id', 'talla__mx_cm', 'talla__orden', 'tipo__via')
             .annotate(n=Count('pk'))
             .order_by('tipo__padre__nombre', 'color__nombre', 'talla__orden'))

    bases = {b.pk: b for b in TipoArticulo.objects.filter(
        pk__in={f['tipo__padre_id'] for f in filas if f['tipo__padre_id']})}
    colores = {c.pk: c for c in Color.objects.filter(pk__in={f['color_id'] for f in filas})}

    resultado = {}  # base_id -> {'base', 'colores': {color_id: dict}, 'total'}
    for f in filas:
        base = bases.get(f['tipo__padre_id'])
        if base is None:  # par de una variante sin padre: no hay a qué modelo base asignarlo
            continue
        entrada = resultado.setdefault(
            base.pk, {'base': base, 'colores': {}, 'total': 0})
        fila_color = entrada['colores'].setdefault(
            f['color_id'], {'color': colores[f['color_id']], 'tallas': {}, 'total': 0})
        celda = fila_color['tallas'].setdefault(
            f['talla__mx_cm'], dict.fromkeys(VIAS, 0))
        celda[f['tipo__via']] += f['n']
        fila_color['total'] += f['n']
        entrada['total'] += f['n']

    return [{'base': e['base'], 'total': e['total'], 'colores': list(e['colores'].values())}
            for e in resultado.values()]


def _ventas_bot_sin_par():
    items = (PedidoItem.objects
             .filter(sku_snapshot=SKU_BOT, pedido__origen=Pedido.TIENDA)
             .exclude(pedido__estado=Pedido.CANCELADO)
             .annotate(ligados=Count('pares'))
             .filter(cantidad__gt=F('ligados'))
             .select_related('pedido').order_by('-pedido__fecha', '-pk'))
    items = list(items)
    if not items:
        return []
    # Tipos y alias se cargan una sola vez para todos los ítems.
    tipos = list(TipoArticulo.objects.all())
    aliases = cargar_aliases()
    resultado = []
    for item in items:
        tipo = resolver_tipo(item.nombre_snapshot, tipos, aliases)
        # Solo variantes de tenis: lo histórico resuelto a un base u otro tipo no es descuadre.
        if tipo is None or tipo.padre_id is None or not tipo.usa_tallas:
            continue
        resultado.append({'item': item, 'faltan': item.cantidad - item.ligados})
    return resultado


def _vendidos_bot_sin_escaneo():
    ultimo_origen = (MovimientoPar.objects
                     .filter(par=OuterRef('pk'), a_estado=Par.Estado.VENDIDO)
                     .order_by('-creado_at', '-pk').values('origen')[:1])
    return (Par.objects.filter(estado=Par.Estado.VENDIDO)
            .annotate(ultimo_origen=Subquery(ultimo_origen))
            .filter(ultimo_origen='bot').count())


def alertas():
    limite = timezone.now() - timedelta(days=DIAS_LOTE_CON_FALTANTES)
    pendientes = Par.objects.filter(lote=OuterRef('pk'), estado=Par.Estado.POR_RECIBIR)
    lotes = (Lote.objects.filter(cerrado_at__isnull=True, creado_at__lt=limite)
             .filter(Exists(pendientes)).order_by('creado_at'))
    return {
        'ventas_bot_sin_par': _ventas_bot_sin_par(),
        'lotes_abiertos_con_faltantes': list(lotes),
        'vendidos_bot_sin_escaneo': _vendidos_bot_sin_escaneo(),
    }
