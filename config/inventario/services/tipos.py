"""Conversión de un tipo existente en modelo base + variante por vía."""
import re
import string
from decimal import Decimal

from django.db import transaction

from catalog.models import SUFIJOS_VIA, TipoArticulo

_LETRA_VIA = {'aereo': 'A', 'maritimo': 'M'}
_VOCALES = set('AEIOU')


def _alnum(texto: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', texto.upper())


def _tallo_palabra(palabra: str) -> str:
    """Primera palabra: su inicial + la segunda consonante que le sigue
    ('Jordan' -> 'JD'); si no la hay, la primera; si tampoco, la letra siguiente."""
    p = _alnum(palabra)
    if not p:
        return ''
    resto = p[1:]
    consonantes = [c for c in resto if c.isalpha() and c not in _VOCALES]
    if len(consonantes) >= 2:
        return p[0] + consonantes[1]
    if consonantes:
        return p[0] + consonantes[0]
    return p[:2]


def _tallo(nombre: str) -> str:
    palabras = nombre.split()
    tallo = _tallo_palabra(palabras[0]) if palabras else ''
    for p in palabras[1:]:
        tallo += _alnum(p)[:1]
    tallo = tallo[:3]
    if len(tallo) < 2:
        tallo = (tallo + 'X')[:2]
    return tallo


def _candidatos(nombre: str, via: str) -> list:
    """Prefijos posibles en orden de preferencia (sin duplicados)."""
    letra = _LETRA_VIA[via]
    tallo = _tallo(nombre)
    # 3er char: 2..9 primero (conserva el comportamiento histórico), luego A-Z y 0-9.
    ciclo = [str(d) for d in range(2, 10)] + list(string.ascii_uppercase) + list(string.digits)
    candidatos = [tallo + letra] + [tallo[:2] + c + letra for c in ciclo]
    # Último recurso: base de 4 chars (+ letra de vía = 5, el máximo del formato).
    cuatro = _alnum(nombre)[:4]
    if len(cuatro) == 4:
        candidatos.append(cuatro + letra)
        candidatos += [cuatro[:3] + c + letra for c in string.ascii_uppercase + string.digits]
    return list(dict.fromkeys(candidatos))


def sugerir_prefijo(nombre: str, via: str) -> str:
    """Prefijo libre para una variante: iniciales del nombre (hasta 3 chars)
    + 'A'/'M'. Ej: 'Jordan 4' aéreo -> 'JD4A'. Si existe, el 3er char pasa a
    2..9, luego A-Z y 0-9, luego bases de 4 chars; si todos están
    ocupados levanta ValueError."""
    for c in _candidatos(nombre, via):
        if not TipoArticulo.objects.filter(prefijo=c).exists():
            return c
    raise ValueError(f'Sin prefijo libre para "{nombre}" ({via}).')


def crear_variante(base: TipoArticulo, via: str, *, prefijo: str | None = None,
                   costo: Decimal | None = None) -> TipoArticulo:
    """Crea (o devuelve, si ya existe) la variante `via` de `base`."""
    with transaction.atomic():
        existente = base.variante(via)
        if existente:
            return existente
        sufijos = SUFIJOS_VIA[via]
        keywords = ', '.join(
            f'{kw} {s}' for kw in base.keywords_list for s in sufijos)
        variante = TipoArticulo(
            nombre=f'{base.nombre} {_LETRA_VIA[via]}',
            keywords=keywords,
            costo=costo if costo is not None else base.costo,
            padre=base, via=via, usa_tallas=True,
            prefijo=prefijo or sugerir_prefijo(base.nombre, via),
        )
        variante.full_clean()
        variante.save()
        if not base.usa_tallas:
            base.usa_tallas = True
            base.save(update_fields=['usa_tallas'])
        return variante
