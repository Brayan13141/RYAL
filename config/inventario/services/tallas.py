"""Corregir la talla de pares capturados con la talla equivocada."""
from django.db import transaction

from inventario.models import MovimientoPar, Par, fmt_talla

# Un par vendido ya está en un pedido con su talla; uno de baja ya no está en la tienda.
CORREGIBLES = (Par.Estado.POR_RECIBIR, Par.Estado.EN_STOCK)


def corregir_talla(pares, talla, *, usuario, nota=''):
    """Pasa `pares` a `talla` y deja la corrección en el historial de cada uno.

    Todo o nada: ValueError si alguno no se puede corregir. Devuelve los pares
    que cambiaron (los que ya tenían esa talla se omiten)."""
    nota = (nota or '').strip()
    if not talla.activa:
        raise ValueError(f'La talla {fmt_talla(talla.mx_cm)} no está activa.')
    with transaction.atomic():
        bloqueados = list(Par.objects.select_for_update(of=('self',))
                          .select_related('talla').filter(pk__in=[p.pk for p in pares])
                          .order_by('pk'))
        if not bloqueados:
            raise ValueError('Elige al menos un par.')
        malos = [p.codigo for p in bloqueados if p.estado not in CORREGIBLES]
        if malos:
            raise ValueError('Solo se corrige la talla de pares por recibir o en stock: '
                             + ', '.join(malos))
        cambiados = []
        for par in bloqueados:
            if par.talla_id == talla.pk:
                continue
            antes = fmt_talla(par.talla.mx_cm)
            par.talla = talla
            par.save(update_fields=['talla'])
            texto = f'Talla corregida: {antes} → {fmt_talla(talla.mx_cm)}'
            MovimientoPar.objects.create(
                par=par, de_estado=par.estado, a_estado=par.estado, usuario=usuario,
                origen='manual', nota=f'{texto}. {nota}' if nota else texto)
            cambiados.append(par)
        return cambiados
