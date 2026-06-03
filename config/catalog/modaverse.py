# -*- coding: utf-8 -*-
"""Utilidades compartidas para URLs de modaverse.

El catálogo guarda el supplier_url en dos formatos según cuándo se cargó:
  - nuevo:  https://www.modaverse.vip/#/proinfo/{pid}
  - viejo:  https://www.modaverse.vip/#/product/{categoryId}?pid={pid}
El productId (pid) es el identificador estable; comparar por pid (no por el
string completo de la URL) reconcilia ambos formatos y evita duplicados.
"""
import re

_PID_RE = re.compile(r'/proinfo/(\w+)|[?&]pid=(\w+)')


def pid_from_url(url: str | None) -> str | None:
    """Extrae el productId de cualquier formato de URL modaverse, o None."""
    m = _PID_RE.search(url or '')
    if not m:
        return None
    return m.group(1) or m.group(2)


def parse_specifications(spec_list):
    """Convierte productSpecificationsList de modaverse en {'sizes': [...], 'colors': [...]}.

    - Agrupa por foreignLanguageName1 (case-insensitive):
        'talla'/'size'/'尺寸'/'尺码' → sizes ; 'color'/'颜色' → colors.
      Otras dimensiones se ignoran.
    - Valor visible = foreignLanguageName2; si vacío, fallback a specificationsValue.
    - Dedup dentro de cada dimensión preservando orden de aparición.
    - Tolera None / lista vacía.
    """
    sizes, colors = [], []
    for entry in (spec_list or []):
        dim = (entry.get('foreignLanguageName1') or '').strip().lower()
        val = (entry.get('foreignLanguageName2') or '').strip() \
            or (entry.get('specificationsValue') or '').strip()
        if not val:
            continue
        if 'talla' in dim or 'size' in dim or '尺寸' in dim or '尺码' in dim:
            if val not in sizes:
                sizes.append(val)
        elif 'color' in dim or '颜色' in dim:
            if val not in colors:
                colors.append(val)
        # otras dimensiones → ignorar silenciosamente
    return {'sizes': sizes, 'colors': colors}
