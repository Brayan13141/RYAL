# -*- coding: utf-8 -*-
"""Arma entradas de shopCarList de modaverse.

Puro a propósito: sin red, sin ORM y sin navegador. Entra el dict de producto que
devuelve catalog.modaverse_api.get_product() y sale la entrada que el carrito de
modaverse espera. Todo lo testeable del armado del pedido vive acá.
"""
import json

from catalog.modaverse import clean_spec_value, esta_agotado

_SIZE_KEYS  = ('talla', 'size', '尺寸', '尺码')
_COLOR_KEYS = ('color', '颜色')


def parse_variant(variant: str) -> tuple:
    """Extrae (talla, color) de variant_target.

      "Talla L / Rojo burdeos" → ("L", "Rojo burdeos")
      "Talla L"                → ("L", "")
      "Rojo burdeos"           → ("", "Rojo burdeos")
      "Blanco · Talla 26"      → ("26", "Blanco")
      ""                       → ("", "")

    El sitio arma variant_target de dos formas cuando hay talla y color
    (orders/views.py): "Talla {size} / {color}" en el modo normal, y
    "{color} · Talla {size}" en el modo colorway por imagen (el que usa
    calzado) — ahí el color va primero y el separador es " · ".
    """
    if not variant:
        return '', ''
    if ' · Talla ' in variant:
        color_part, size_part = variant.split(' · Talla ', 1)
        return size_part.strip(), color_part.strip()
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

    Si el producto tiene una dimensión (talla o color) que variant_target no
    trajo, el ítem se agrega igual (status='added') y notas avisa cuál
    dimensión quedó sin elegir. No se bloquea: casi ningún pedido local
    registra color (la BD apenas tiene 2 de ~26k productos con variantes de
    color) mientras que en Modaverse esa dimensión es común, así que bloquear
    haría fallar casi todos los pedidos. Misma lógica que stock_warnings:
    avisar, no bloquear.
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
            # conserva.
            elegidos.append(disponibles[0])
            avisos.append(f'Sin variante — seleccionado "{spec_label(disponibles[0])}"')
        else:
            # El pedido sí trae variant_target pero cubre solo una dimensión
            # (p. ej. "Talla M" sin color) y el producto tiene la otra. No se
            # inventa un valor — rellenar con el primero rompería el carrito
            # si el cliente pidió otro color — pero tampoco desaparece en
            # silencio: se avisa para que el operador la complete a mano.
            etiqueta = disponibles[0].get('foreignLanguageName1') or dimension
            avisos.append(f'"{etiqueta}" sin elegir')

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


def _stock(product: dict):
    stock = product.get('stockNum')
    if isinstance(stock, (int, float)) and not isinstance(stock, bool):
        return stock
    return None


def falta_stock(product: dict, quantity: int) -> bool:
    """Misma regla que el carrito de modaverse: pedir más de lo que hay, salvo que
    el producto se venda sin stock (ynStockForZero '1', "散货" en su sitio).

    La regla vive en `catalog.modaverse.esta_agotado`, compartida con el scraper y
    con el sync de stock de la tienda.
    """
    return esta_agotado(_stock(product), product.get('ynStockForZero'), quantity)


def stock_warnings(product: dict, quantity: int) -> list:
    """Advertencias que no bloquean: el ítem se agrega igual (decisión de Bryan)."""
    avisos = []
    if str(product.get('ynLaunch') or '') == '0':
        avisos.append('despublicado en Modaverse')
    if falta_stock(product, quantity):
        stock = int(_stock(product))
        if stock <= 0:
            # stockNum llega negativo cuando está sobrevendido: "stock -1 < 1" no
            # se entiende de un vistazo en el panel.
            avisos.append(f'AGOTADO en Modaverse (stock {stock})')
        else:
            avisos.append(f'stock {stock} < {quantity} pedidas')
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
