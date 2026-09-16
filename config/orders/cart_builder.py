# -*- coding: utf-8 -*-
"""Arma entradas de shopCarList de modaverse.

Puro a propósito: sin red, sin ORM y sin navegador. Entra el dict de producto que
devuelve catalog.modaverse_api.get_product() y sale la entrada que el carrito de
modaverse espera. Todo lo testeable del armado del pedido vive acá.
"""
import json

from catalog.modaverse import clean_spec_value

_SIZE_KEYS  = ('talla', 'size', '尺寸', '尺码')
_COLOR_KEYS = ('color', '颜色')


def parse_variant(variant: str) -> tuple:
    """Extrae (talla, color) de variant_target.

      "Talla L / Rojo burdeos" → ("L", "Rojo burdeos")
      "Talla L"                → ("L", "")
      "Rojo burdeos"           → ("", "Rojo burdeos")
      ""                       → ("", "")
    """
    if not variant:
        return '', ''
    if variant.startswith('Talla') and ' / ' in variant:
        size_part, color_part = variant.split(' / ', 1)
        return size_part.removeprefix('Talla').strip(), color_part.strip()
    if variant.startswith('Talla'):
        return variant.removeprefix('Talla').strip(), ''
    return '', variant.strip()


def spec_dimension(spec: dict) -> str:
    """'size', 'color' o '' para una entrada de productSpecificationsList.

    foreignLanguageName1 llega en minúscula para talla y capitalizado para color,
    así que la comparación va siempre en minúsculas.
    """
    dim = (spec.get('foreignLanguageName1') or '').strip().lower()
    if any(k in dim for k in _SIZE_KEYS):
        return 'size'
    if any(k in dim for k in _COLOR_KEYS):
        return 'color'
    return ''


def spec_label(spec: dict) -> str:
    """Valor visible en español; cae al valor original si no hay traducción."""
    raw = (spec.get('foreignLanguageName2') or '').strip() \
        or (spec.get('specificationsValue') or '').strip()
    return clean_spec_value(raw)


def _find_spec(specs, dimension: str, wanted: str):
    opciones = [s for s in specs if spec_dimension(s) == dimension]
    w = wanted.strip().casefold()
    for s in opciones:
        if spec_label(s).casefold() == w:
            return s
    for s in opciones:                     # respaldo: el valor chino original
        if (s.get('specificationsValue') or '').strip().casefold() == w:
            return s
    return None


def _build_guige(specs, selected_ids: set) -> list:
    """Todas las opciones agrupadas por dimensión, con isSelect en las elegidas."""
    orden, grupos = [], {}
    for s in specs:
        key = (s.get('specificationsName') or '', s.get('foreignLanguageName1') or '')
        if key not in grupos:
            grupos[key] = []
            orden.append(key)
        grupos[key].append({
            'productSpecificationsId': s.get('productSpecificationsId'),
            'specificationsValue':     s.get('specificationsValue'),
            'unitPrice':               s.get('unitPrice'),
            'foreignLanguageName2':    s.get('foreignLanguageName2'),
            'isSelect':                s.get('productSpecificationsId') in selected_ids,
        })
    return [
        {'specificationsName': zh, 'foreignLanguageName1': es, 'zhiList': grupos[(zh, es)]}
        for zh, es in orden
    ]


def build_cart_entry(product: dict, variant_target: str, quantity: int,
                     category_name: str = '') -> tuple:
    """Devuelve (entrada, status, notas).

    status es 'added' o 'variant_not_found'. Cuando no es 'added', la entrada es
    None y el ítem NO entra al carrito: mejor un carrito corto y un aviso que uno
    completo con la talla equivocada.
    """
    specs = product.get('productSpecificationsList') or []
    size_value, color_value = parse_variant(variant_target)

    elegidos, faltantes, avisos = [], [], []

    for dimension, wanted in (('size', size_value), ('color', color_value)):
        disponibles = [s for s in specs if spec_dimension(s) == dimension]
        if not disponibles:
            continue
        if wanted:
            spec = _find_spec(specs, dimension, wanted)
            if spec is None:
                faltantes.append(wanted)
            else:
                elegidos.append(spec)
        elif not variant_target:
            # No se registró ninguna variante (ni talla ni color): el código
            # viejo tomaba la primera opción de cada dimensión y avisaba; se
            # conserva. Si el pedido sí trae variant_target pero solo cubre una
            # dimensión (p. ej. "Talla M" sin color), la otra se deja sin
            # seleccionar en vez de rellenarla con un valor no pedido.
            elegidos.append(disponibles[0])
            avisos.append(f'Sin variante — seleccionado "{spec_label(disponibles[0])}"')

    if faltantes:
        opciones = ', '.join(dict.fromkeys(spec_label(s) for s in specs)) or 'ninguna'
        return None, 'variant_not_found', (
            f'No existe la variante "{" / ".join(faltantes)}". Opciones: {opciones}'
        )

    entry = dict(product)
    entry['categoryName']            = category_name
    entry['foreignLanguageName']     = category_name
    entry['orderSpecificationsList'] = elegidos
    entry['guige'] = _build_guige(
        specs, {s.get('productSpecificationsId') for s in elegidos}
    )
    entry['num'] = quantity

    return entry, 'added', '; '.join(avisos)


def stock_warnings(product: dict, quantity: int) -> list:
    """Advertencias que no bloquean: el ítem se agrega igual (decisión de Bryan)."""
    avisos = []
    if str(product.get('ynLaunch') or '') == '0':
        avisos.append('despublicado en Modaverse')
    stock = product.get('stockNum')
    if isinstance(stock, (int, float)) and not isinstance(stock, bool) and stock < quantity:
        avisos.append(f'stock {int(stock)} < {quantity} pedidas')
    return avisos


def build_cart_script(entries: list) -> str:
    """El snippet JS que el operador pega en la consola de modaverse.

    Fusiona shopCarList en el objeto 'user' que ya existe para preservar el
    userToken de la sesión. location.reload() es necesario: el router SPA de Vue
    cambia la ruta sin recargar, y el store en memoria no se actualizaría.
    """
    if not entries:
        return ''
    cart_json = json.dumps(entries, ensure_ascii=False)
    return (
        "(function(){"
        f"var c={cart_json};"
        "console.log('[ryal] Inyectando', c.length, 'ítem(s):', "
        "c.map(function(i){return (i.productName||i.name||'?')+'(×'+(i.num||1)+')';}));"
        "var u=JSON.parse(localStorage.getItem('user')||'{}');"
        "u.shopCarList=c;"
        "localStorage.setItem('user',JSON.stringify(u));"
        "console.log('[ryal] ✅ Carrito inyectado. Recargando página...');"
        "location.reload();"
        "})()"
    )
