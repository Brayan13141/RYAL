from django.test import TestCase

from catalog.models import Category, Product, ProductImage
from orders.minimos import (
    Faltante, Linea, empeora, faltantes, lineas_de_carrito, lineas_de_pedido,
)
from orders.models import Order, OrderItem
from orders.variantes import nombre_variante
from orders.views import _cart_key, _get_category_violations


class MinimosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.gorras = Category.objects.create(name='Gorras', slug='gorras', min_order_qty=20)
        cls.calzado = Category.objects.create(
            name='Calzado', slug='calzado', min_qty_per_item=12, has_color_variants=True,
        )
        cls.gorra = cls._producto('G1', 'Gorra lisa', cls.gorras)
        cls.tenis = cls._producto('T1', 'Tenis Air', cls.calzado)
        cls.blanco = ProductImage.objects.create(
            product=cls.tenis, image='products/a.jpg', color_label='Blanco', display_order=0)
        cls.negro = ProductImage.objects.create(
            product=cls.tenis, image='products/b.jpg', color_label='Negro', display_order=1)

    @staticmethod
    def _producto(sku, nombre, categoria):
        return Product.objects.create(
            sku=sku, name=nombre, category=categoria,
            base_price=100,
        )

    # ── helpers ──────────────────────────────────────────────────────────
    def _cart(self, *specs):
        """specs: (producto, cantidad, {size, img, color}) → carrito con claves reales."""
        cart = {}
        for s in specs:
            product, qty = s[0], s[1]
            kw = s[2] if len(s) > 2 else {}
            size, img, color = kw.get('size'), kw.get('img'), kw.get('color')
            key = _cart_key(product.pk, None, size, img.pk if img else None, color)
            item = {
                'product_id': product.pk,
                'quantity': qty,
                'variant_name': nombre_variante(size, color, img, None, None),
            }
            if img:
                item['image_pk'] = img.pk
            cart[key] = item
        return cart

    def _pedido(self, *specs):
        order = Order.objects.create(customer_name='X', customer_phone='4440000000')
        for s in specs:
            product, qty = s[0], s[1]
            kw = s[2] if len(s) > 2 else {}
            OrderItem.objects.create(
                order=order, product=product, quantity=qty, price_snapshot=100,
                sku_snapshot=product.sku, name_snapshot=product.name,
                variant_snapshot=nombre_variante(
                    kw.get('size'), kw.get('color'), kw.get('img'), None, None),
            )
        return order

    def _resumen(self, d):
        return {(f.nombre, f.actual, f.minimo) for f in d.values()}

    # ── tests ────────────────────────────────────────────────────────────
    def test_carrito_y_pedido_dan_mismos_faltantes(self):
        specs = [
            (self.tenis, 5, {'size': '26', 'img': self.blanco}),
            (self.tenis, 4, {'size': '27', 'img': self.blanco}),
            (self.gorra, 10),
        ]
        cart = self._cart(*specs)
        order = self._pedido(*specs)
        del_carrito = self._resumen(faltantes(lineas_de_carrito(cart)))
        self.assertEqual(del_carrito, self._resumen(faltantes(lineas_de_pedido(order.items.all()))))
        self.assertEqual(del_carrito, {('Tenis Air · Blanco', 9, 12), ('Gorras', 10, 20)})

    def test_tallas_del_mismo_colorway_se_suman(self):
        specs = [
            (self.tenis, 5, {'size': '26', 'img': self.blanco}),
            (self.tenis, 7, {'size': '27', 'img': self.blanco}),
        ]
        self.assertEqual(faltantes(lineas_de_carrito(self._cart(*specs))), {})
        order = self._pedido(*specs)
        self.assertEqual(faltantes(lineas_de_pedido(order.items.all())), {})

    def test_colorways_distintos_no_se_suman(self):
        specs = [
            (self.tenis, 6, {'size': '26', 'img': self.blanco}),
            (self.tenis, 6, {'size': '26', 'img': self.negro}),
        ]
        esperado = {('Tenis Air · Blanco', 6, 12), ('Tenis Air · Negro', 6, 12)}
        self.assertEqual(self._resumen(faltantes(lineas_de_carrito(self._cart(*specs)))), esperado)
        order = self._pedido(*specs)
        self.assertEqual(self._resumen(faltantes(lineas_de_pedido(order.items.all()))), esperado)

    def test_talla_con_color_texto_se_agrupa_por_producto(self):
        specs = [
            (self.tenis, 6, {'size': 'L', 'color': 'Rojo'}),
            (self.tenis, 6, {'size': 'L', 'color': 'Azul'}),
        ]
        self.assertEqual(faltantes(lineas_de_carrito(self._cart(*specs))), {})
        order = self._pedido(*specs)
        self.assertEqual(faltantes(lineas_de_pedido(order.items.all())), {})

    def test_renglon_individual_sin_talla(self):
        cart = self._cart((self.tenis, 5, {'img': self.blanco}))
        f = faltantes(lineas_de_carrito(cart))
        self.assertEqual(list(f), [('renglon', self.tenis.pk, 'Blanco')])
        self.assertEqual(f[('renglon', self.tenis.pk, 'Blanco')].missing, 7)

    def test_raiz_suma_todo(self):
        otra = self._producto('G2', 'Gorra trucker', self.gorras)
        cart = self._cart((self.gorra, 8), (otra, 8))
        self.assertEqual(
            list(faltantes(lineas_de_carrito(cart)).values()),
            [Faltante('Gorras', 16, 20)],
        )

    def test_renglon_sin_producto_se_ignora(self):
        order = self._pedido((self.gorra, 25))
        OrderItem.objects.create(
            order=order, product=None, quantity=3, price_snapshot=1,
            sku_snapshot='X', name_snapshot='Borrado',
        )
        self.assertEqual(faltantes(lineas_de_pedido(order.items.all())), {})
        cart = {'999999_none': {'product_id': 999999, 'quantity': 1, 'variant_name': ''}}
        self.assertEqual(lineas_de_carrito(cart), [])

    def test_empeora_detecta_nuevo_y_mas_grande_y_tolera_igual_o_menor(self):
        a = {('raiz', 1): Faltante('Gorras', 16, 20)}
        self.assertEqual(empeora(a, {('raiz', 1): Faltante('Gorras', 16, 20)}), [])
        self.assertEqual(empeora(a, {('raiz', 1): Faltante('Gorras', 18, 20)}), [])
        self.assertEqual(len(empeora(a, {('raiz', 1): Faltante('Gorras', 15, 20)})), 1)
        self.assertEqual(len(empeora({}, {('raiz', 1): Faltante('Gorras', 19, 20)})), 1)

    def test_orden_de_violaciones_individuales_grupos_raices(self):
        cart = self._cart(
            (self.gorra, 5),
            (self.tenis, 3, {'size': '26', 'img': self.blanco}),
            (self.tenis, 2, {'img': self.negro}),
        )
        nombres = [v['name'] for v in _get_category_violations(cart)]
        self.assertEqual(nombres, ['Tenis Air', 'Tenis Air · Blanco', 'Gorras'])

    def test_linea_es_inmutable(self):
        linea = Linea(self.gorra, 1, '', None, 'Gorra')
        with self.assertRaises(Exception):
            linea.quantity = 2
