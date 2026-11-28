"""Llevar el costo de un tipo a sus pares sin vender.

El costo de un par se graba al crear el lote; cambiar después el costo del tipo
no lo toca. Esto lo actualiza a pedido, solo en pares que aún no se venden.
"""
from collections import Counter

from django.db import transaction

from inventario.models import MovimientoPar, Par

SIN_VENDER = (Par.Estado.POR_RECIBIR, Par.Estado.EN_STOCK)


def pares_con_otro_costo(tipo, costo):
    """Pares sin vender de `tipo` cuyo costo no es `costo`."""
    return (Par.objects.filter(tipo=tipo, estado__in=SIN_VENDER)
            .exclude(costo=costo).select_related('lote'))


def resumen_por_lote(pares):
    """[(lote, costo actual, cantidad)] para mostrar qué se va a cambiar."""
    cuenta = Counter((p.lote, p.costo) for p in pares)
    return sorted(((lote, costo, n) for (lote, costo), n in cuenta.items()),
                  key=lambda f: (f[0].pk, f[1]))


def actualizar_costo_pares(tipo, *, usuario):
    """Pone el costo del tipo en sus pares sin vender. Devuelve (actualizados, omitidos).

    Inventario inicial: el costo es el total. Con proveedor: se ajusta el precio del
    proveedor y el flete del lote se conserva (costo = proveedor + flete); si el costo
    nuevo es menor que el flete, el par se omite.
    """
    actualizados = omitidos = 0
    with transaction.atomic():
        for par in pares_con_otro_costo(tipo, tipo.costo).select_for_update(of=('self',)):
            viejo = par.costo
            if par.precio_proveedor is not None:
                flete = par.flete or 0
                if tipo.costo < flete:
                    omitidos += 1
                    continue
                par.precio_proveedor = tipo.costo - flete
            par.costo = tipo.costo
            par.save(update_fields=['costo', 'precio_proveedor'])
            MovimientoPar.objects.create(
                par=par, de_estado=par.estado, a_estado=par.estado, usuario=usuario,
                origen='edicion',
                nota=f'Costo actualizado {viejo:.2f} → {tipo.costo:.2f} al editar el tipo.')
            actualizados += 1
    return actualizados, omitidos
