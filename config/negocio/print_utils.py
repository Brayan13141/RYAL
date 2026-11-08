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


