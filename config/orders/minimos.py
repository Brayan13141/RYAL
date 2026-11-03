"""Núcleo de mínimos de compra, compartido por el carrito/checkout y la edición
de pedidos del panel.

Tres reglas, todas sobre la categoría RAÍZ del producto:
  - root.min_order_qty > 1     → total de piezas de la raíz (gorras: 20).
  - root.min_qty_per_item > 0  → piezas por modelo/color (calzado: 12). Las
    líneas con talla se suman por grupo (modelo, colorway); el resto se valida
    renglón por renglón.

El carrito y el pedido se traducen a `Linea` y comparten el mismo cálculo, para
que lo que el sitio exige al comprar sea lo que el panel exige al editar.
"""
from collections.abc import Iterable
from dataclasses import dataclass

from catalog.models import Product, ProductImage

from .cart_builder import parse_variant


@dataclass(frozen=True)
class Linea:
    product: Product
    quantity: int
    # item['variant_name'] en el carrito / variant_snapshot en el pedido.
    variante: str
    # None → el renglón se valida solo; tupla → se suma con las del mismo grupo.
    grupo_modelo: tuple | None
    etiqueta_modelo: str


@dataclass(frozen=True)
class Faltante:
    nombre: str
    actual: int
    minimo: int

    @property
    def missing(self):
        return self.minimo - self.actual


def _raiz(product):
    cat = product.category
    return cat.parent if cat.parent_id else cat


def faltantes(lineas):
    """{clave: Faltante} de lo que no cumple el mínimo.

    Claves: ('raiz', root.pk), grupo_modelo, ('renglon', product.pk, variante).
    El orden de salida es el del checkout: individuales, grupos de tallas, raíces.
    """
    individuales = {}
    grupos = {}
    raices = {}

    for ln in lineas:
        root = _raiz(ln.product)

        if root.min_qty_per_item > 0:
            if ln.grupo_modelo is not None:
                g = grupos.setdefault(
                    ln.grupo_modelo, {'nombre': ln.etiqueta_modelo, 'qty': 0, 'min': root.min_qty_per_item})
                g['qty'] += ln.quantity
            else:
                clave = ('renglon', ln.product.pk, ln.variante)
                r = individuales.setdefault(
                    clave, {'nombre': ln.product.name, 'qty': 0, 'min': root.min_qty_per_item})
                r['qty'] += ln.quantity

        if root.min_order_qty > 1:
            r = raices.setdefault(
                ('raiz', root.pk), {'nombre': root.name, 'qty': 0, 'min': root.min_order_qty})
            r['qty'] += ln.quantity

    resultado = {}
    for bloque in (individuales, grupos, raices):
        for clave, v in bloque.items():
            if v['qty'] < v['min']:
                resultado[clave] = Faltante(v['nombre'], v['qty'], v['min'])
    return resultado


def lineas_de_carrito(cart):
    """Líneas del carrito en sesión. Omite productos que ya no existen."""
    if not cart:
        return []

    products_by_id = {
        p.pk: p
        for p in Product.objects.select_related('category__parent')
        .filter(pk__in=[item['product_id'] for item in cart.values()])
    }
    img_pks = [
        item['image_pk'] for key, item in cart.items()
        if '_size_' in key and item.get('image_pk')
    ]
    imgs_by_pk = (
        {img.pk: img for img in ProductImage.objects.filter(pk__in=img_pks)}
        if img_pks else {}
    )

    lineas = []
    for key, item in cart.items():
        product = products_by_id.get(item['product_id'])
        if not product:
            continue
        grupo, etiqueta = None, product.name
        if '_size_' in key:
            img_pk = item.get('image_pk')   # None para talla pura
            grupo = ('modelo', product.pk, img_pk)
            if img_pk:
                img = imgs_by_pk.get(img_pk)
                color = img.color_label if (img and img.color_label) else 'variante'
                etiqueta = f'{product.name} · {color}'
        lineas.append(Linea(product, item['quantity'], item.get('variant_name', ''), grupo, etiqueta))
    return lineas


def lineas_de_pedido(items: Iterable):
    """Líneas de los OrderItem de un pedido, con el mismo agrupado que el carrito."""
    lineas = []
    for it in items:
        product = it.product
        if product is None:
            continue
        variante = it.variant_snapshot or ''
        talla, color = parse_variant(variante)
        grupo, etiqueta = None, product.name
        if talla:
            if ' · Talla ' in variante:
                grupo = ('modelo', product.pk, color)
                etiqueta = f'{product.name} · {color}'
            else:
                # Talla pura o "Talla X / color texto": el carrito no tiene image_pk.
                grupo = ('modelo', product.pk, None)
        lineas.append(Linea(product, it.quantity, variante, grupo, etiqueta))
    return lineas


def empeora(antes, despues):
    """Faltantes de `despues` que son nuevos o más grandes que en `antes`."""
    return [
        f for clave, f in despues.items()
        if clave not in antes or f.actual < antes[clave].actual
    ]
