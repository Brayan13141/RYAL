"""Lecturas del escáner: recibir pares de un lote y deshacer la última lectura."""
import re
from dataclasses import dataclass

from django.db import transaction

from inventario.models import (
    Conteo, LecturaConteo, LecturaRepetida, Lote, MovimientoPar, Par, TransicionInvalida)

_RE_URL = re.compile(r'/panel/inventario/p/([^/?#\s]+)', re.IGNORECASE)


@dataclass
class Resultado:
    nivel: str  # 'verde' | 'amarillo' | 'rojo'
    mensaje: str
    par: Par | None = None
    movimiento_id: int | None = None


def _rojo(mensaje, par=None):
    return Resultado('rojo', mensaje, par, None)


def extraer_codigo(lectura):
    """Código del par a partir de la URL del QR (cualquier host) o del código suelto."""
    texto = (lectura or '').strip()
    coincide = _RE_URL.search(texto)
    if coincide:
        texto = coincide.group(1)
    return texto.strip().upper()


def recibir(lectura, *, lote, usuario):
    codigo = extraer_codigo(lectura)
    if not codigo:
        return _rojo('Código no existe')
    # Leer, decidir y actuar bajo el mismo candado: dos lecturas simultáneas
    # del mismo par dan verde y amarillo, no verde y rojo.
    with transaction.atomic():
        par = Par.objects.select_for_update(of=('self',)).select_related('lote').filter(
            codigo=codigo).first()
        if par is None:
            return _rojo('Código no existe')
        if par.lote_id != lote.pk:
            # La referencia es opcional en el alta: el número siempre identifica el lote.
            ref = f' ({par.lote.referencia})' if par.lote.referencia else ''
            return _rojo(f'Este par es del lote #{par.lote.pk}{ref}', par)

        if par.estado == Par.Estado.ANULADO:
            return _rojo('Etiqueta anulada: este par se quitó del lote. Retira la etiqueta.', par)
        if par.estado == Par.Estado.EN_STOCK:
            # Queda registrada para la pantalla de "Escaneados 2 veces".
            LecturaRepetida.objects.create(par=par, lote=lote, usuario=usuario)
            return Resultado('amarillo', 'Ya estaba recibido', par, None)
        if par.estado == Par.Estado.POR_RECIBIR:
            mensaje = 'Recibido'
        elif par.estado == Par.Estado.BAJA and par.motivo_baja == 'faltante':
            mensaje = 'Apareció'
        else:
            return _rojo(
                f'No se puede recibir: el par está en estado {par.get_estado_display()}', par)

        try:
            mov = par.transicionar('en_stock', usuario=usuario, origen='escaner')
        except TransicionInvalida as e:
            return _rojo(str(e), par)
        return Resultado('verde', mensaje, par, mov.pk)


def deshacer(movimiento_id, *, usuario, nota):
    """Revierte una recepción, solo si es la última lectura del par y con nota."""
    nota = (nota or '').strip()
    if not nota:
        return _rojo('Escribe el motivo para deshacer')
    with transaction.atomic():
        mov = MovimientoPar.objects.filter(pk=movimiento_id).first()
        if mov is None:
            return _rojo('La lectura no existe')
        # Candado del par antes de comprobar que sea su última lectura.
        par = Par.objects.select_for_update().get(pk=mov.par_id)
        if par.movimientos.order_by('-creado_at', '-pk').first().pk != mov.pk:
            return _rojo('Solo se puede deshacer la última lectura del par', par)

        if (mov.de_estado, mov.a_estado) == ('baja', 'en_stock'):
            # Volver a faltante (en_stock -> baja) no está en la tabla de transiciones.
            return _rojo('No se puede deshacer esta lectura', par)
        if (mov.de_estado, mov.a_estado) != ('por_recibir', 'en_stock'):
            return _rojo('Esta lectura no se deshace aquí', par)

        try:
            nuevo = par.transicionar(
                'por_recibir', usuario=usuario, origen='deshacer', nota=nota)
        except TransicionInvalida as e:
            return _rojo(str(e), par)
        return Resultado('verde', 'Lectura deshecha', par, nuevo.pk)


def contar(lectura, *, conteo, usuario):
    """Registra una lectura en un conteo. Nunca cambia `Par.estado`."""
    codigo = extraer_codigo(lectura)
    # Un QR ajeno (URL larga) no cabe en la columna: rojo sin registrar, no un 500.
    if not codigo or len(codigo) > LecturaConteo._meta.get_field('codigo_leido').max_length:
        return _rojo('Código no existe')
    with transaction.atomic():
        # Candado del conteo: serializa lecturas y comprueba que siga abierto.
        conteo = Conteo.objects.select_for_update().get(pk=conteo.pk)
        if conteo.cerrado_at is not None:
            return _rojo('Este conteo ya está cerrado')
        par = Par.objects.filter(codigo=codigo).first()
        if conteo.lecturas.filter(codigo_leido=codigo).exists():
            return Resultado('amarillo', 'Ya contado en este conteo', par, None)
        LecturaConteo.objects.create(conteo=conteo, par=par, codigo_leido=codigo)
        if par is None:
            return _rojo('Código no existe')
        if par.estado == Par.Estado.EN_STOCK:
            return Resultado('verde', 'Contado', par, None)
        return Resultado(
            'amarillo', f'No debería estar aquí: {par.get_estado_display()}', par, None)
