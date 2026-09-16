import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from catalog.models import Category, Product
from orders.models import Order, OrderItem, SupplierOrder, SupplierOrderItem
from orders.tests_cart_builder import FOG0124, ZA156

MOD = 'orders.management.commands.sync_modaverse_order'


class SyncModaverseOrderTests(TestCase):

    def setUp(self):
        self.cat = Category.objects.create(name='Playeras', slug='playeras')
        self.order = Order.objects.create(customer_name='Bryan', customer_phone='4451112233')
        self.supplier_order = SupplierOrder.objects.create(order=self.order)

    @staticmethod
    def _url(pid):
        return f'https://www.modaverse.vip/#/proinfo/{pid}'

    def _producto(self, sku, name, pid):
        """Un Product nuestro apuntando a un pid de modaverse.

        base_price no tiene default en el modelo (catalog/models.py), así que es
        obligatorio acá o el create revienta.
        """
        return Product.objects.create(
            sku=sku, name=name, category=self.cat, base_price=200,
            supplier_url=self._url(pid) if pid else '',
        )

    def _item(self, producto, variant, qty):
        """Un OrderItem del pedido más su SupplierOrderItem pendiente.

        Separado de _producto a propósito: el caso que importa es el mismo
        Product con dos variantes distintas, no dos Products.
        """
        oi = OrderItem.objects.create(
            order=self.order, product=producto, quantity=qty,
            price_snapshot=500, sku_snapshot=producto.sku,
            name_snapshot=producto.name, variant_snapshot=variant,
        )
        return SupplierOrderItem.objects.create(
            supplier_order=self.supplier_order, order_item=oi,
            supplier_url=producto.supplier_url, variant_target=variant,
            status='pending',
        )

    @staticmethod
    def _entradas_del_script(script):
        """Extrae el array de ítems del snippet JS."""
        return json.loads(script[script.find('var c=') + 6:script.find(';console.log')])

    def test_arma_el_carrito_y_marca_added(self):
        prod = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        si = self._item(prod, 'Talla M', 2)
        with patch(f'{MOD}.get_product', return_value=FOG0124):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        si.refresh_from_db()
        self.supplier_order.refresh_from_db()
        self.assertEqual(si.status, 'added')
        self.assertEqual(self.supplier_order.status, 'done')
        self.assertIn('FOG0124', self.supplier_order.cart_script)

    def test_mismo_producto_dos_tallas_cantidades_distintas(self):
        """El bug de qty_map: las dos tallas salían con la misma cantidad.

        La dimensión talla va primera en orderSpecificationsList porque
        build_cart_entry procesa size antes que color.
        """
        prod   = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        s_item = self._item(prod, 'Talla S', 2)
        m_item = self._item(prod, 'Talla M', 5)
        with patch(f'{MOD}.get_product', return_value=FOG0124):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.supplier_order.refresh_from_db()
        arr = self._entradas_del_script(self.supplier_order.cart_script)
        self.assertEqual(len(arr), 2)
        por_talla = {
            e['orderSpecificationsList'][0]['foreignLanguageName2']: e['num'] for e in arr
        }
        self.assertEqual(por_talla, {'S': 2, 'M': 5})
        s_item.refresh_from_db(); m_item.refresh_from_db()
        self.assertEqual(s_item.status, 'added')
        self.assertEqual(m_item.status, 'added')

    def test_variante_inexistente_no_entra_y_el_pedido_sale_parcial(self):
        prod = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        ok   = self._item(prod, 'Talla M', 1)
        bad  = self._item(prod, 'Talla XXXL', 1)
        with patch(f'{MOD}.get_product', return_value=FOG0124):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        ok.refresh_from_db(); bad.refresh_from_db(); self.supplier_order.refresh_from_db()
        self.assertEqual(ok.status, 'added')
        self.assertEqual(bad.status, 'variant_not_found')
        self.assertIn('XXXL', bad.notes)
        self.assertEqual(self.supplier_order.status, 'partial')

    def test_sin_stock_entra_igual_con_aviso(self):
        prod = self._producto('ZA-156', 'ZA-156', '2062727000623153154')
        si = self._item(prod, '', 3)
        with patch(f'{MOD}.get_product', return_value=ZA156):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        si.refresh_from_db()
        self.assertEqual(si.status, 'added')
        self.assertIn('despublicado', si.notes)
        self.assertIn('stock', si.notes)

    def test_producto_inexistente_en_la_api(self):
        prod = self._producto('X', 'Fantasma', 'PRNOPE')
        si = self._item(prod, '', 1)
        with patch(f'{MOD}.get_product', return_value=None):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        si.refresh_from_db()
        self.assertEqual(si.status, 'variant_not_found')
        self.assertIn('no existe', si.notes.lower())

    def test_api_caida_deja_el_pedido_failed_y_sin_script(self):
        from catalog.modaverse_api import ModaverseUnavailable
        prod = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        self._item(prod, 'Talla M', 1)
        with patch(f'{MOD}.get_product', side_effect=ModaverseUnavailable('cerrada')):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.supplier_order.refresh_from_db()
        self.assertEqual(self.supplier_order.status, 'failed')
        self.assertEqual(self.supplier_order.cart_script, '')

    def test_item_sin_url_se_marca_no_url_sin_llamar_a_la_api(self):
        """Pendiente pero sin supplier_url: se resuelve sin gastar una llamada.

        Ojo al escribirlo: si el ítem se crea ya en 'no_url', el comando corta
        antes por "no hay ítems pendientes" y el test pasa sin probar nada.
        """
        prod = self._producto('SINURL', 'Sin proveedor', '')
        si = self._item(prod, '', 1)
        self.assertEqual(si.status, 'pending')
        with patch(f'{MOD}.get_product') as mock_get:
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        mock_get.assert_not_called()
        si.refresh_from_db()
        self.assertEqual(si.status, 'no_url')

    def test_ya_no_acepta_headless(self):
        from django.core.management import CommandError
        with self.assertRaises(CommandError):
            call_command('sync_modaverse_order', self.order.pk, '--headless', stdout=StringIO())
