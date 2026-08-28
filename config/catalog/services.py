import datetime
from decimal import Decimal


def consumir_uso(codigo: str = None, *, pk: int = None) -> bool:
    """Incrementa usos_actuales respetando usos_max en UN solo UPDATE atómico.

    Retorna False (sin incrementar) si el código ya está agotado. Usar SIEMPRE
    esto en vez de leer usos_actuales y luego incrementar en pasos separados:
    dos checkouts simultáneos podían rebasar usos_max con el patrón viejo.
    """
    from django.db.models import F, Q
    from .models import CodigoDescuento
    if pk is not None:
        qs = CodigoDescuento.objects.filter(pk=pk)
    else:
        qs = CodigoDescuento.objects.filter(codigo__iexact=codigo)
    return qs.filter(
        Q(usos_max__isnull=True) | Q(usos_actuales__lt=F('usos_max'))
    ).update(usos_actuales=F('usos_actuales') + 1) > 0


def normalizar_texto(texto: str) -> str:
    """Minúsculas y espacios colapsados: la forma canónica en que se guardan
    y se comparan los alias. Es la misma normalización que ya hace
    `TipoArticulo.matches()`, para que alias y keywords vean el mismo texto."""
    return ' '.join((texto or '').lower().split())


def buscar_tipo_articulo(texto: str, tipos=None):
    """Devuelve el TipoArticulo cuya keyword coincidente más larga (más
    específica) aparezca en texto, o None si ninguna coincide.

    Varios tipos pueden compartir una keyword genérica (ej. "gorras"); si el
    texto también contiene una keyword más específica de otro tipo (ej. "new
    era"), esa debe ganar en vez del primero por orden alfabético de nombre.

    `tipos`: lista ya cargada de TipoArticulo. Sin ella cada llamada consulta
    la tabla completa, lo que en un reporte que recorre cientos de líneas se
    vuelve una query por línea.
    """
    from .models import TipoArticulo
    texto_norm = ' '.join((texto or '').lower().split())
    if not texto_norm:
        return None

    if tipos is None:
        tipos = TipoArticulo.objects.all()

    mejor_tipo = None
    mejor_len = -1
    for tipo in tipos:
        for kw in tipo.keywords_list:
            kw_norm = ' '.join(kw.split()).lower()
            if kw_norm in texto_norm and len(kw_norm) > mejor_len:
                mejor_tipo = tipo
                mejor_len = len(kw_norm)
    return mejor_tipo


def sugerencias_de_tipo(texto, tipos=None, limite=3, piso=0.45):
    """Tipos que se PARECEN al texto, para ofrecerlos cuando ninguno matcheó.

    Puntúa contra el nombre del tipo Y contra cada una de sus keywords, y se
    queda con el mejor de los dos: 'yezzy' se parece poco a «Tenis yeezy»
    pero mucho a su keyword 'yeezy', y es la keyword la que sabe que es el
    mismo producto.

    `piso` corta el ruido: sin él cualquier texto corto "se parece" a todo.
    Devolver vacío es una respuesta válida — el bot sabe decirlo.
    """
    from difflib import SequenceMatcher
    from .models import TipoArticulo

    texto_norm = normalizar_texto(texto)
    if not texto_norm:
        return []

    if tipos is None:
        tipos = TipoArticulo.objects.all()

    puntuados = []
    for tipo in tipos:
        candidatos = [tipo.nombre] + tipo.keywords_list
        mejor = max(
            (SequenceMatcher(None, texto_norm, normalizar_texto(c)).ratio()
             for c in candidatos),
            default=0.0,
        )
        if mejor >= piso:
            puntuados.append((mejor, tipo))

    # Desempate por nombre para que el orden sea estable entre corridas.
    puntuados.sort(key=lambda par: (-par[0], par[1].nombre))
    return [tipo for _, tipo in puntuados[:limite]]


def validar_codigo(
    codigo: str,
    descriptions: list = None,
    canal: str = None,
    categories: list = None,
    items: list = None,
) -> dict:
    """
    Valida y calcula el descuento de un código.

    items: lista preferida — [{'qty': int, 'description': str, 'root_category_id': int|None}]
           Habilita cálculo por ítem (tipo_descuento='por_item') y scope matching preciso.
    descriptions + categories: modo legacy (qty=1 por entrada), usado cuando items=None.
    canal: 'negocio' | 'web' | None (sin filtro de canal).

    Retorna dict: {valido, descuento, mensaje, codigo_id, tipo_nombre}
    """
    from .models import CodigoDescuento
    _invalid = {'valido': False, 'descuento': 0, 'mensaje': '', 'codigo_id': None, 'tipo_nombre': None}

    try:
        code = CodigoDescuento.objects.select_related('tipo_articulo', 'categoria_web').get(
            codigo__iexact=codigo, is_active=True
        )
    except CodigoDescuento.DoesNotExist:
        return {**_invalid, 'mensaje': 'Código inválido o inactivo.'}

    if code.valid_hasta and code.valid_hasta < datetime.date.today():
        return {**_invalid, 'mensaje': 'Código expirado.'}

    if code.usos_max is not None and code.usos_actuales >= code.usos_max:
        return {**_invalid, 'mensaje': 'Código agotado.'}

    if canal and code.canal != 'ambos' and code.canal != canal:
        canal_labels = {'negocio': 'el panel/bot', 'web': 'la tienda web'}
        return {**_invalid, 'mensaje': f'Código solo disponible en {canal_labels.get(code.canal, code.canal)}.'}

    # Normalizar a lista uniforme para scope matching y cálculo
    if items is not None:
        items_norm = items
    else:
        descs = list(descriptions or [])
        cats  = list(categories or [])
        items_norm = [
            {
                'qty': 1,
                'description': desc,
                'root_category_id': cats[i][1] if i < len(cats) else None,
            }
            for i, desc in enumerate(descs)
        ]

    # Scope matching → qualifying items
    if code.tipo_articulo:
        tipo = code.tipo_articulo
        qualifying = [it for it in items_norm if tipo.matches(it.get('description', ''))]
        if not qualifying:
            return {**_invalid, 'mensaje': f'Código solo aplica a {tipo.nombre}.'}
    elif code.categoria_web:
        target_id = code.categoria_web_id
        qualifying = [it for it in items_norm if it.get('root_category_id') == target_id]
        if not qualifying:
            return {**_invalid, 'mensaje': f'Código solo aplica a {code.categoria_web.name}.'}
    else:
        qualifying = list(items_norm)

    # Calcular monto del descuento
    unit_amount = float(code.descuento)
    if code.tipo_descuento == 'por_item':
        total_qty = sum(it.get('qty', 1) for it in qualifying)
        descuento_final = unit_amount * total_qty
    else:
        descuento_final = unit_amount

    return {
        'valido': True,
        'descuento': descuento_final,
        'mensaje': f'Descuento de ${descuento_final:.0f} MXN aplicado.',
        'codigo_id': code.pk,
        'tipo_nombre': code.tipo_articulo.nombre if code.tipo_articulo_id else None,
    }
