"""Venta de tenis por el bot de WhatsApp: vía, color y talla salen del texto, y
los pares salen del inventario físico.

`faltantes_de_texto` es el ÚNICO lugar que decide qué le falta a un texto de
venta; lo usan `crear_pedido_tienda_bot` y la búsqueda de la API. No se copia
la regla.
"""
import re
from decimal import Decimal, InvalidOperation

from inventario.models import Color, Par, TallaCalzado, fmt_talla

_TALLA_RE = re.compile(r'(?<!\w)[tT]\s?(\d{2}(?:[.,]5)?)(?!\w)')

MSG_TALLA = 'Falta la talla: escríbela como t27 o t26.5'


def _buscar_talla(texto):
    """(talla|None, mensaje|None). Un token que parece talla pero no existe
    (o está inactiva) también es falta: no se adivina otra talla."""
    m = _TALLA_RE.search(texto)
    if m is None:
        return None, MSG_TALLA
    token = m.group(1).replace(',', '.')
    try:
        valor = Decimal(token)
    except InvalidOperation:
        return None, MSG_TALLA
    talla = TallaCalzado.objects.filter(mx_cm=valor, activa=True).first()
    if talla is None:
        return None, (f'Falta la talla: t{token} no existe en el catálogo. '
                      'Escríbela como t27 o t26.5')
    return talla, None


def _sin_nombre_del_modelo(norm, variante):
    """Quita del texto la keyword más larga de la variante que aparece y el nombre
    del modelo base: un color dentro del nombre ('Jordan 4 Black Cat') no cuenta."""
    presentes = [k for k in (Color.normalizar(k) for k in variante.keywords_list)
                 if k and re.search(rf'(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])', norm)]
    quitar = [max(presentes, key=len)] if presentes else []
    quitar.append(Color.normalizar(variante.modelo_base.nombre))
    for frase in quitar:
        if frase:
            norm = re.sub(rf'(?<![a-z0-9]){re.escape(frase)}(?![a-z0-9])', ' ', norm)
    return norm


def _buscar_color(texto, variante):
    """(color|None, mensaje|None). Nombre/alias normalizado, solo como palabra
    o frase completa; gana el más largo y un empate entre colores distintos es
    ambiguo. El nombre del modelo no se busca: se quita antes."""
    colores = list(variante.colores_efectivos())
    norm = _sin_nombre_del_modelo(Color.normalizar(texto), variante)
    mejor_len = 0
    ganadores = {}
    for color in colores:
        for nombre in [color.nombre, *color.alias_list]:
            if not nombre:
                continue
            if re.search(rf'(?<![a-z0-9]){re.escape(nombre)}(?![a-z0-9])', norm):
                if len(nombre) > mejor_len:
                    mejor_len, ganadores = len(nombre), {color.pk: color}
                elif len(nombre) == mejor_len:
                    ganadores[color.pk] = color
    if len(ganadores) == 1:
        return next(iter(ganadores.values())), None
    if len(ganadores) > 1:
        nombres = ', '.join(sorted(c.nombre for c in ganadores.values()))
        return None, f'Color ambiguo: el texto menciona varios ({nombres}). Escribe uno solo.'
    nombres = ', '.join(c.nombre for c in colores) or 'sin colores cargados'
    return None, f'Falta el color de {variante.modelo_base.nombre}: {nombres}'


def _analizar(texto, variante):
    """[(falta, mensaje)] más el color y la talla encontrados."""
    color, msg_color = _buscar_color(texto, variante)
    talla, msg_talla = _buscar_talla(texto)
    faltas = []
    if msg_color:
        faltas.append(('color', msg_color))
    if msg_talla:
        faltas.append(('talla', msg_talla))
    return color, talla, faltas


def parsear_venta_tenis(texto, variante):
    """(color|None, talla|None, mensajes de lo que falta)."""
    color, talla, faltas = _analizar(texto, variante)
    return color, talla, [m for _, m in faltas]


def faltantes_de_texto(texto, tipo):
    """Lo que le falta a un texto de venta ya resuelto a `tipo`:
    `[{'texto', 'falta': 'via'|'color'|'talla', 'mensaje'}]`, [] si está completo."""
    if tipo.tiene_variantes:
        kw = (tipo.keywords_list or [tipo.nombre])[0]
        return [{
            'texto': texto, 'falta': 'via',
            'mensaje': (f'{tipo.nombre}: ¿aéreo o marítimo? '
                        f'Escríbelo como «{kw} aereo …» o «{kw} maritimo …»'),
        }]
    if tipo.es_variante and tipo.usa_tallas:
        _, _, faltas = _analizar(texto, tipo)
        return [{'texto': texto, 'falta': f, 'mensaje': m} for f, m in faltas]
    return []


def seleccionar_pares(variante, color, talla, cantidad):
    """Bloquea (sin esperar a otros) hasta `cantidad` pares en stock, los más
    antiguos primero. Devuelve una lista ya evaluada."""
    return list(
        Par.objects.select_for_update(skip_locked=True)
        .filter(tipo=variante, color=color, talla=talla, estado=Par.Estado.EN_STOCK)
        .order_by('recibido_at', 'id')[:cantidad])


def asignar_pares(item, variante, color, talla, cantidad, *, usuario=None, pares=None):
    """Pasa a `vendido` los pares del ítem. Devuelve cuántos asignó.

    `pares`: los ya seleccionados con `seleccionar_pares` (hace falta
    seleccionarlos antes para calcular el costo del ítem antes de grabarlo)."""
    if pares is None:
        pares = seleccionar_pares(variante, color, talla, cantidad)
    for par in pares:
        par.transicionar('vendido', usuario=usuario, origen='bot', pedido_item=item)
    return len(pares)


def mensaje_sin_stock(variante, color, talla, faltan):
    return (f'⚠️ Sin stock de {variante.nombre} {color.nombre} {fmt_talla(talla.mx_cm)} '
            f'(faltó {faltan}). Revisa el inventario.')
