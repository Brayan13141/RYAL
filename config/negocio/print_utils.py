def _build_label_json(product, image_url=None):
    """Formato JSON para Bluetooth Print app — etiqueta de producto 58mm."""
    entries = [
        {"type": 0, "content": "RYAL SNEAKERS",           "bold": 1, "align": 1, "format": 0},
        {"type": 0, "content": "================================", "bold": 0, "align": 1, "format": 0},
    ]
    if image_url:
        entries.append({"type": 1, "path": image_url, "align": 1, "width": 80})
        entries.append({"type": 0, "content": " ", "bold": 0, "align": 0, "format": 0})
    entries += [
        {"type": 0, "content": product.name,              "bold": 1, "align": 1, "format": 1},
        {"type": 0, "content": f"SKU: {product.sku}",     "bold": 0, "align": 1, "format": 4},
        {"type": 0, "content": "--------------------------------", "bold": 0, "align": 0, "format": 0},
        {"type": 3, "value": product.sku, "size": 180,    "align": 1},
        {"type": 0, "content": " ",                        "bold": 0, "align": 0, "format": 0},
    ]
    return {str(i): e for i, e in enumerate(entries)}


def _build_receipt_json(pedido):
    """Formato JSON para Bluetooth Print app — ticket de venta.
    Llama a pedido.items.all() y pedido.pagos.all() — usar prefetch_related en la vista."""
    fecha = pedido.fecha.strftime('%d/%m/%Y')
    pago = next(iter(pedido.pagos.all()), None)
    metodo = pago.get_metodo_pago_display() if pago else 'Efectivo'

    entries = [
        {"type": 0, "content": "RYAL SNEAKERS",         "bold": 1, "align": 1, "format": 0},
        {"type": 0, "content": "TICKET DE VENTA",       "bold": 0, "align": 1, "format": 0},
        {"type": 0, "content": "================================", "bold": 0, "align": 1, "format": 0},
        {"type": 0, "content": f"#{pedido.pk}",          "bold": 0, "align": 1, "format": 0},
        {"type": 0, "content": f"Fecha: {fecha}",        "bold": 0, "align": 0, "format": 0},
        {"type": 0, "content": " ",                      "bold": 0, "align": 0, "format": 0},
    ]
    for item in pedido.items.all():
        entries.append({
            "type": 0,
            "content": f"{item.nombre_snapshot} x{item.cantidad}  ${item.subtotal:.2f}",
            "bold": 0, "align": 0, "format": 0,
        })
    entries += [
        {"type": 0, "content": "--------------------------------", "bold": 0, "align": 0, "format": 0},
        {"type": 0, "content": f"TOTAL: ${pedido.precio_venta:.2f}", "bold": 1, "align": 0, "format": 0},
        {"type": 0, "content": f"Pago: {metodo}",       "bold": 0, "align": 0, "format": 0},
        {"type": 0, "content": " ",                      "bold": 0, "align": 0, "format": 0},
        {"type": 0, "content": "Gracias por tu compra", "bold": 0, "align": 1, "format": 0},
        {"type": 0, "content": "ryalsneackers.com",     "bold": 0, "align": 1, "format": 0},
        {"type": 0, "content": " ",                      "bold": 0, "align": 0, "format": 0},
    ]
    return {str(i): e for i, e in enumerate(entries)}
