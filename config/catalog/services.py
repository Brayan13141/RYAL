import datetime
from decimal import Decimal


def buscar_tipo_articulo(texto: str):
    """Devuelve el primer TipoArticulo cuyas keywords hagan match con texto, o None."""
    from .models import TipoArticulo
    for tipo in TipoArticulo.objects.all():
        if tipo.matches(texto):
            return tipo
    return None


def validar_codigo(codigo: str, descriptions: list, canal: str = None, categories: list = None) -> dict:
    """
    Valida un código de descuento.
    descriptions: nombres de ítems (para matching por tipo_articulo en negocio).
    canal: 'negocio' | 'web' | None (sin filtro).
    categories: nombres de categorías de los ítems (para matching por categoria_web en web).
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

    # Validar canal si se especifica
    if canal and code.canal != 'ambos' and code.canal != canal:
        canal_labels = {'negocio': 'el panel/bot', 'web': 'la tienda web'}
        return {**_invalid, 'mensaje': f'Código solo disponible en {canal_labels.get(code.canal, code.canal)}.'}

    # Validar scope por tipo de artículo (negocio)
    if code.tipo_articulo:
        tipo = code.tipo_articulo
        matched = any(tipo.matches(desc) for desc in descriptions)
        if not matched:
            return {**_invalid, 'mensaje': f'Código solo aplica a {tipo.nombre}.'}

    # Validar scope por categoría web
    if code.categoria_web:
        cat_name = code.categoria_web.name.lower()
        cats = [c.lower() for c in (categories or [])]
        if not any(cat_name in c or c in cat_name for c in cats):
            return {**_invalid, 'mensaje': f'Código solo aplica a {code.categoria_web.name}.'}

    return {
        'valido': True,
        'descuento': float(code.descuento),
        'mensaje': f'Descuento de ${code.descuento} MXN aplicado.',
        'codigo_id': code.pk,
        'tipo_nombre': code.tipo_articulo.nombre if code.tipo_articulo_id else None,
    }
