"""Alta de lotes (manual, CSV o inventario inicial) y su cierre."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from inventario.codigos import generar_codigo
from inventario.models import Lote, MovimientoPar, Par, TarifaFlete

_CENTAVOS = Decimal('0.01')


def _validar_linea(n, linea, via, origen):
    """Revisa una línea y devuelve sus datos limpios; ValueError en español si falla."""
    tipo = linea['tipo']
    if not tipo.es_variante or tipo.via != via:
        raise ValueError(
            f'Línea {n}: "{tipo.nombre}" no es una variante de vía {via}.')
    if not tipo.prefijo:
        raise ValueError(f'Línea {n}: "{tipo.nombre}" no tiene prefijo para el código.')
    color = linea['color']
    if not tipo.colores_efectivos().filter(pk=color.pk).exists():
        raise ValueError(
            f'Línea {n}: el color "{color.nombre}" no está asignado a "{tipo.nombre}".')
    cantidad = linea['cantidad']
    if not isinstance(cantidad, int) or isinstance(cantidad, bool) or cantidad <= 0:
        raise ValueError(f'Línea {n}: la cantidad debe ser un entero mayor que cero.')
    precio = linea.get('precio_proveedor')
    if origen == 'inicial':
        precio = None
    elif precio is None:
        raise ValueError(f'Línea {n}: falta el precio del proveedor de "{tipo.nombre}".')
    else:
        precio = Decimal(precio)
        if precio < 0:
            raise ValueError(f'Línea {n}: el precio del proveedor no puede ser negativo.')
    return tipo, color, linea['talla'], cantidad, precio


def crear_lote(*, via, origen, lineas, usuario, referencia='', notas='', token_sha=None):
    """Crea el lote con un par `por_recibir` por unidad. Todo o nada."""
    if not lineas:
        raise ValueError('El lote necesita al menos una línea.')
    # Se valida todo antes de escribir: una línea mala no deja nada creado.
    limpias = [_validar_linea(n, l, via, origen) for n, l in enumerate(lineas, 1)]
    try:
        flete = TarifaFlete.objects.get(via=via).monto_por_par
    except TarifaFlete.DoesNotExist:
        raise ValueError(f'No hay tarifa de flete para la vía {via}.')
    flete = Decimal(flete).quantize(_CENTAVOS)

    with transaction.atomic():
        lote = Lote.objects.create(
            referencia=referencia, origen=origen, via=via, flete_por_par=flete,
            creado_por=usuario, notas=notas, token_sha=token_sha)
        for tipo, color, talla, cantidad, precio in limpias:
            if origen == 'inicial':
                precio_par, flete_par = None, None
                costo = Decimal(tipo.costo).quantize(_CENTAVOS)
            else:
                precio_par, flete_par = precio.quantize(_CENTAVOS), flete
                costo = precio_par + flete_par
            for _ in range(cantidad):
                par = Par.objects.create(
                    codigo=generar_codigo(tipo.prefijo), tipo=tipo, color=color,
                    talla=talla, lote=lote, precio_proveedor=precio_par,
                    flete=flete_par, costo=costo)
                # Alta: no hay estado previo, por eso no pasa por `transicionar`.
                MovimientoPar.objects.create(
                    par=par, de_estado='', a_estado=Par.Estado.POR_RECIBIR,
                    usuario=usuario, origen='alta')
    return lote


def cerrar_lote(lote, *, usuario):
    """Lo que no se escaneó pasa a baja/faltante. Devuelve cuántos; idempotente."""
    with transaction.atomic():
        pendientes = list(lote.pares.filter(estado=Par.Estado.POR_RECIBIR))
        for par in pendientes:
            par.transicionar(
                'baja', usuario=usuario, origen='cierre_lote', motivo='faltante')
        if lote.cerrado_at is None:
            lote.cerrado_at = timezone.now()
            lote.save(update_fields=['cerrado_at'])
    return len(pendientes)
