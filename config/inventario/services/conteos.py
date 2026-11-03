"""Resumen de un conteo físico y correcciones par por par."""
from django.db import transaction

from inventario.models import MovimientoPar, Par, TransicionInvalida
from inventario.services.escaneo import Resultado, _rojo, extraer_codigo


def _esperados(conteo):
    """Pares en stock: ahora si el conteo está abierto; al cerrar si ya se cerró."""
    base = Par.objects.select_related('tipo__padre', 'color', 'talla')
    if conteo.cerrado_at is None:
        return list(base.filter(estado=Par.Estado.EN_STOCK))
    # Estado al cierre = destino del último movimiento anterior al cierre.
    ultimo = {}
    for mov in (MovimientoPar.objects.filter(creado_at__lte=conteo.cerrado_at)
                .order_by('creado_at', 'pk').values_list('par_id', 'a_estado')):
        ultimo[mov[0]] = mov[1]
    ids = [pk for pk, estado in ultimo.items() if estado == Par.Estado.EN_STOCK]
    return list(base.filter(pk__in=ids))


def resumen_conteo(conteo):
    lecturas = list(conteo.lecturas.select_related(
        'par__tipo__padre', 'par__color', 'par__talla'))
    leidos = {l.par_id: l.par for l in lecturas if l.par_id}
    esperados = _esperados(conteo)
    por_codigo = lambda p: p.codigo  # noqa: E731

    no_leidos = sorted((p for p in esperados if p.pk not in leidos), key=por_codigo)
    fuera = sorted((p for p in leidos.values() if p.estado != Par.Estado.EN_STOCK),
                   key=por_codigo)

    grupos = {}
    for p in esperados:
        base = p.tipo.modelo_base
        clave = (base.pk, p.tipo.via, p.color_id, p.talla_id)
        g = grupos.setdefault(clave, {
            'base': base, 'via': p.tipo.via, 'color': p.color, 'talla': p.talla,
            'esperados': 0, 'leidos': 0})
        g['esperados'] += 1
        if p.pk in leidos:
            g['leidos'] += 1
    combinaciones = sorted(grupos.values(), key=lambda g: (
        g['base'].nombre, g['via'] or '', g['color'].nombre, g['talla'].mx_cm))
    return {
        'no_leidos': no_leidos,
        'fuera_de_stock': fuera,
        'por_combinacion': combinaciones,
        'desconocidos': [l.codigo_leido for l in lecturas if l.par_id is None],
    }


ACCIONES = ('perdido', 'recibir', 'aparecio')


def corregir(conteo, codigo, accion, nota, *, usuario):
    """Aplica una corrección del resumen a un par, con nota obligatoria."""
    nota = (nota or '').strip()
    if accion not in ACCIONES:
        return _rojo('Acción desconocida')
    if not nota:
        return _rojo('Escribe una nota para corregir')
    codigo = extraer_codigo(codigo)
    with transaction.atomic():
        par = Par.objects.select_for_update().filter(codigo=codigo).first()
        if par is None:
            return _rojo('Código no existe')
        leido = conteo.lecturas.filter(par=par).exists()
        try:
            if accion == 'perdido':
                if leido:
                    return _rojo('El par fue leído en este conteo', par)
                if par.estado != Par.Estado.EN_STOCK:
                    return _rojo(f'El par está en estado {par.get_estado_display()}', par)
                par.transicionar('baja', usuario=usuario, origen='manual',
                                 motivo='perdido', nota=nota)
                return Resultado('verde', 'Marcado como perdido', par, None)
            if not leido:
                return _rojo('El par no fue leído en este conteo', par)
            if accion == 'recibir':
                if par.estado != Par.Estado.POR_RECIBIR:
                    return _rojo(f'El par está en estado {par.get_estado_display()}', par)
                par.transicionar('en_stock', usuario=usuario, origen='manual', nota=nota)
                return Resultado('verde', 'Recibido', par, None)
            if par.estado != Par.Estado.BAJA or par.motivo_baja != 'faltante':
                return _rojo('Solo un faltante puede aparecer', par)
            par.transicionar('en_stock', usuario=usuario, origen='manual', nota=nota)
            return Resultado('verde', 'Apareció', par, None)
        except TransicionInvalida as e:
            return _rojo(str(e), par)
