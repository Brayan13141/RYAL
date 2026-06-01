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
