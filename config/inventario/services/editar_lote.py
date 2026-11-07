"""Editar un lote abierto: cambiar cuántos pares tiene cada renglón (tipo × color × talla)
y sumarle combinaciones nuevas. Lo que sobra se anula, nunca se borra: la etiqueta pudo
haberse impreso y el escáner debe reconocerla."""
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count, Max, Q

from inventario.models import Lote, MovimientoPar, Par, TransicionInvalida
from inventario.services.lotes import _validar_linea, crear_pares

MAX_POR_RENGLON = 500
_CAMBIO = ('El lote cambió mientras lo editabas (alguien escaneó o lo editó). '
           'Recarga la página y vuelve a capturar.')

# Estados que ya no se pueden anular: el par se escaneó (o se vendió / dio de baja).
_FIJOS = Q(estado__in=[Par.Estado.EN_STOCK, Par.Estado.VENDIDO, Par.Estado.BAJA])


@dataclass
class Resultado:
    creados: list = field(default_factory=list)
    anulados: list = field(default_factory=list)


def version_lote(lote):
    """Cambia con cualquier movimiento de un par del lote (alta, escaneo, anulación...).
    La pantalla de edición la manda de vuelta para no pisar cambios hechos mientras tanto."""
    return MovimientoPar.objects.filter(par__lote=lote).aggregate(v=Max('pk'))['v'] or 0


def filas_editables(lote):
    """Renglones del lote con pares no anulados: cuántos tiene (`activos`) y el mínimo al
    que se puede bajar (`minimo`: los que ya no están por recibir)."""
    return [{
        'tipo_id': r['tipo_id'], 'color_id': r['color_id'], 'talla_id': r['talla_id'],
        'tipo': r['tipo__nombre'], 'color': r['color__nombre'], 'talla_mx': r['talla__mx_cm'],
        'activos': r['activos'], 'minimo': r['minimo'],
    } for r in (lote.pares.exclude(estado=Par.Estado.ANULADO)
                .values('tipo_id', 'color_id', 'talla_id',
                        'tipo__nombre', 'color__nombre', 'talla__mx_cm')
                .annotate(activos=Count('pk'), minimo=Count('pk', filter=_FIJOS))
                .order_by('tipo__nombre', 'color__nombre', 'talla__orden'))]


def _cantidad(valor, nombre):
    if not isinstance(valor, int) or isinstance(valor, bool) or valor < 0:
        raise ValueError(f'{nombre}: la cantidad debe ser un entero de 0 o más.')
    if valor > MAX_POR_RENGLON:
        raise ValueError(f'{nombre}: máximo {MAX_POR_RENGLON} pares por talla.')
    return valor


def editar_lote(lote, *, cantidades, lineas, usuario, version):
    """Aplica la edición completa o nada.

    `cantidades`: {(tipo_id, color_id, talla_id): cuántos debe tener el renglón}.
    `lineas`: combinaciones a sumar, con el formato de `crear_lote` (ya en la variante de
    la vía del lote). Si caen en un renglón existente, se suman a él.
    `version`: la de `version_lote` al abrir la pantalla; None no la revisa."""
    with transaction.atomic():
        # Candado del lote: dos ediciones a la vez se serializan.
        lote = Lote.objects.select_for_update().get(pk=lote.pk)
        if lote.cerrado_at is not None:
            raise ValueError('El lote está cerrado: ya no se puede editar.')
        if version is not None and version != version_lote(lote):
            raise ValueError(_CAMBIO)

        filas = {(f['tipo_id'], f['color_id'], f['talla_id']): f for f in filas_editables(lote)}
        objetivo = {}
        for clave, valor in cantidades.items():
            fila = filas.get(clave)
            if fila is None:
                raise ValueError('Uno de los renglones no está en el lote. Recarga la página.')
            nombre = f'{fila["tipo"]} {fila["color"]} {fila["talla_mx"].normalize():f}'
            n = _cantidad(valor, nombre)
            if n < fila['minimo']:
                raise ValueError(
                    f'{nombre}: no puede quedar en {n}, ya hay {fila["minimo"]} '
                    f'escaneados (o vendidos / dados de baja).')
            objetivo[clave] = n

        origen = 'inicial' if lote.origen == 'inicial' else 'manual'
        nuevas = {}  # clave -> [tipo, color, talla, cantidad, precio]
        for n, linea in enumerate(lineas, 1):
            tipo, color, talla, cantidad, precio = _validar_linea(n, linea, lote.via, origen)
            clave = (tipo.pk, color.pk, talla.pk)
            if clave in nuevas:
                nuevas[clave][3] += cantidad
            else:
                nuevas[clave] = [tipo, color, talla, cantidad, precio]

        res = Resultado()
        for clave, n in objetivo.items():
            delta = n - filas[clave]['activos']
            if delta < 0:
                sobran = (lote.pares.filter(
                    tipo_id=clave[0], color_id=clave[1], talla_id=clave[2],
                    estado=Par.Estado.POR_RECIBIR).order_by('-pk')[:-delta])
                for par in sobran:
                    try:
                        par.transicionar('anulado', usuario=usuario, origen='edicion',
                                         nota='Anulado al editar el lote')
                    except TransicionInvalida:
                        # Se escaneó entre la lectura y el candado del par.
                        raise ValueError(_CAMBIO)
                    res.anulados.append(par)
            elif delta > 0:
                muestra = (lote.pares.filter(tipo_id=clave[0], color_id=clave[1],
                                             talla_id=clave[2])
                           .select_related('tipo', 'color', 'talla').order_by('-pk').first())
                res.creados += crear_pares(
                    lote, muestra.tipo, muestra.color, muestra.talla, delta,
                    muestra.precio_proveedor, usuario=usuario)
        for tipo, color, talla, cantidad, precio in nuevas.values():
            res.creados += crear_pares(lote, tipo, color, talla, cantidad, precio,
                                       usuario=usuario)
    return res
