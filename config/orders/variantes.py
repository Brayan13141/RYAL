def nombre_variante(size_name, color, img_obj, color_num, variant):
    """Texto descriptivo de la variante de un ítem (carrito y edición de pedidos).

    Formatos (los que lee cart_builder.parse_variant):
      talla + color texto  → "Talla L / Rojo"
      talla + colorway     → "Blanco · Talla 26"
      solo color / talla / colorway / ProductVariant, o '' si no hay nada.
    color_num puede ser None (el panel no lo conoce): cae a display_order + 1.
    """
    if size_name and color:
        return f'Talla {size_name} / {color}'
    if size_name and img_obj:
        # Modo combinado: tallas + colorway por imagen → "Blanco · Talla 26"
        color_part = img_obj.color_label or (f'Color {color_num}' if color_num else f'Color {img_obj.display_order + 1}')
        return f'{color_part} · Talla {size_name}'
    if color:
        return color
    if size_name:
        return f'Talla {size_name}'
    if img_obj:
        # Modo colorway puro: solo imagen
        if img_obj.color_label:
            return img_obj.color_label
        if color_num:
            return f'Color {color_num}'
        return f'Color {img_obj.display_order + 1}'
    if variant:
        return variant.name
    return ''
