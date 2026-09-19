from decimal import Decimal
from unittest import mock

from django.test import TestCase

from catalog.models import Category, Product
from orders.models import (
    Order, FUENTE_REGISTRADO, FUENTE_COSTO_ACTUAL, FUENTE_PRECIO_MENOS_100,
)


class CostoConFuenteTests(TestCase):
    """El costo de proveedor de un renglón web, y de dónde salió.

    Para el contador un costo estimado tiene que verse como estimado, así
    que la cadena de respaldo de `costo_mercancia` ahora dice qué eslabón usó.
    """

    def setUp(self):
        self.root = Category.objects.create(
            name='Gorras', slug='gorras',
            profit_margin=Decimal('100'), shipping_cost=Decimal('50'))
        self.product = Product.objects.create(
            sku='RYL-CAP-1', name='Gorra', category=self.root,
            base_price=Decimal('200'))
        self.order = Order.objects.create(
            order_code='CF-1', customer_name='Ana', customer_phone='5512345678')

    def _item(self, cost=None, product=None, price='500', qty=2):
        return self.order.items.create(
            product=product, quantity=qty, price_snapshot=Decimal(price),
            cost_snapshot=Decimal(cost) if cost is not None else None,
            sku_snapshot='SKU', name_snapshot='Producto')

    def test_con_snapshot_es_registrado(self):
        item = self._item(cost='300', product=self.product)
        self.assertEqual(item.costo_con_fuente(), (Decimal('600'), FUENTE_REGISTRADO))

    def test_sin_snapshot_usa_el_costo_actual_del_producto(self):
        item = self._item(product=self.product)
        # base 200 + envío de la raíz 50 = 250 por unidad
        self.assertEqual(item.costo_con_fuente(), (Decimal('500'), FUENTE_COSTO_ACTUAL))

    def test_sin_producto_estima_precio_menos_100(self):
        item = self._item()
        self.assertEqual(item.costo_con_fuente(), (Decimal('800'), FUENTE_PRECIO_MENOS_100))

    def test_si_el_producto_falla_estima_precio_menos_100(self):
        item = self._item(product=self.product)
        with mock.patch.object(Product, 'effective_base_price',
                               new_callable=mock.PropertyMock,
                               side_effect=Exception('sin categoría')):
            self.assertEqual(item.costo_con_fuente(),
                             (Decimal('800'), FUENTE_PRECIO_MENOS_100))

    def test_costo_mercancia_suma_los_renglones(self):
        self._item(cost='300', product=self.product)   # 600
        self._item(product=self.product, qty=1)         # 250
        self._item(qty=1)                                # 400
        self.assertEqual(self.order.costo_mercancia, Decimal('1250'))
