# -*- coding: utf-8 -*-
"""Utilidades compartidas para URLs de modaverse.

El catálogo guarda el supplier_url en dos formatos según cuándo se cargó:
  - nuevo:  https://www.modaverse.vip/#/proinfo/{pid}
  - viejo:  https://www.modaverse.vip/#/product/{categoryId}?pid={pid}
El productId (pid) es el identificador estable; comparar por pid (no por el
string completo de la URL) reconcilia ambos formatos y evita duplicados.
"""
import json as _json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

_PID_RE = re.compile(r'/proinfo/(\w+)|[?&]pid=(\w+)')

# Elimina desde el primer carácter CJK/ideográfico/fullwidth en adelante.
# Cubre: CJK Unified (4E00-9FFF), Extension A (3400-4DBF),
# CJK Symbols & Punctuation (3000-303F), Fullwidth/Halfwidth (FF00-FFEF).
_CJK_SUFFIX_RE = re.compile(r'\s*[　-〿㐀-䶿一-鿿＀-￯].*$')


def _clean_spec_val(s: str) -> str:
    """Quita sufijos de caracteres CJK/fullwidth y whitespace residual."""
    return _CJK_SUFFIX_RE.sub('', s).strip()


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
      El dedup es exact-match / case-sensitive: 'M' y 'm' se consideran
      valores distintos.
    - Tolera None / lista vacía.
    """
    sizes, colors = [], []
    for entry in (spec_list or []):
        dim = (entry.get('foreignLanguageName1') or '').strip().lower()
        raw = (entry.get('foreignLanguageName2') or '').strip() \
            or (entry.get('specificationsValue') or '').strip()
        val = _clean_spec_val(raw)
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


def read_modaverse_json(json_path=None):
    """Lee scraped_modaverse.json del root del repo. Devuelve el dict o None.
    Parámetro json_path opcional para tests (inyección de fixture)."""
    if json_path is None:
        json_path = Path(__file__).resolve().parents[2] / 'scraped_modaverse.json'
    path = Path(json_path)
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return _json.load(f)


def category_filter_ids(categories_tree, keywords) -> set:
    """IDs de categoría (padre + subs) que coinciden con alguna keyword.
    Match en el padre incluye todas sus subs; match en una sub incluye su padre.
    Sin distinción de mayúsculas. Mismo criterio que el scraper --category."""
    kws = [k.lower() for k in keywords]
    ids = set()
    for cat in categories_tree:
        name = (cat.get('name_es') or cat.get('name_zh') or '').lower()
        if any(kw in name for kw in kws):
            ids.add(cat['id'])
            for sub in cat.get('subcategories', []):
                ids.add(sub['id'])
        else:
            for sub in cat.get('subcategories', []):
                sname = (sub.get('name_es') or sub.get('name_zh') or '').lower()
                if any(kw in sname for kw in kws):
                    ids.add(sub['id'])
                    ids.add(cat['id'])
    return ids


def precio_proveedor(p):
    """Precio del proveedor para una entrada del JSON, como Decimal, o None.

    El JSON trae `price_mxn` en 0 para una parte del catálogo; eso no es un
    precio de $0, es un hueco (al crearlos, load_productos cae a un default por
    categoría). Sincronizar contra ese 0 destruiría el precio bueno.
    """
    raw = p.get('price_mxn') or p.get('price_usd') or 0
    try:
        val = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return val if val > 0 else None


def sincronizar_precio(prod, precio):
    """Aplica el precio del proveedor a un Product respetando ediciones manuales.

    `modaverse_price` es el último precio visto del proveedor:
      - si `base_price == modaverse_price`, nadie lo tocó desde la última
        sincronización y el precio se actualiza;
      - si difieren, alguien lo editó en el panel y `base_price` no se toca —
        solo avanza la marca, para seguir al proveedor sin pisar la decisión;
      - si la marca es None (producto anterior a este campo), se adopta sin
        cambiar el precio: no hay forma de saber si fue editado.

    Devuelve la lista de campos que cambiaron, lista para `update_fields`.
    """
    if precio is None:
        return []
    campos = []
    marca = prod.modaverse_price
    if marca is not None and prod.base_price == marca and prod.base_price != precio:
        prod.base_price = precio
        campos.append('base_price')
    if marca != precio:
        prod.modaverse_price = precio
        campos.append('modaverse_price')
    return campos


def merge_scraped_products(existing_products, scraped_products, filter_ids):
    """Fusiona el resultado de una corrida `--category` con el JSON previo.

    Lo recién bajado manda: si la API acaba de devolver un producto para una
    categoría del filtro, esa versión reemplaza a la guardada, esté archivada
    donde esté.

    Preservar solo por `category_id` no alcanza. Una entrada que quedó con
    `category_id` nulo (respuesta degradada de la API → cae en 'General' con
    precio 0 y sin imágenes) no coincide con ningún id del filtro, así que se
    cuela en la lista de preservados y se vuelve **inmune** al mecanismo que
    debería repararla: el scraper la baja completa y la descarta por duplicada.
    Comparar también por sku rompe ese círculo, y de paso evita el sku duplicado
    cuando el proveedor mueve un producto a una categoría del filtro.

    Sin `filter_ids` (corrida completa) no hay nada que preservar: lo scrapeado
    es el JSON entero.
    """
    scraped = list(scraped_products)
    if not filter_ids:
        return scraped

    scraped_skus = {p.get('sku') for p in scraped if p.get('sku')}
    preservados = [
        p for p in existing_products
        if p.get('category_id') not in filter_ids and p.get('sku') not in scraped_skus
    ]
    return preservados + scraped
