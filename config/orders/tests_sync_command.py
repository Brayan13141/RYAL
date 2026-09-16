import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from catalog.models import Category, Product
from orders.models import Order, OrderItem, SupplierOrder, SupplierOrderItem
from orders.tests_cart_builder import FOG0124, ZA156, ZA266

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

    def test_agotado_entra_con_aviso_y_el_pedido_queda_partial(self):
        """Entra al carrito (decisión de Bryan) pero el pedido no puede verse 'done'."""
        prod = self._producto('ZA-266', 'ZA-266', '2082402530293186562')
        si = self._item(prod, '', 1)
        with patch(f'{MOD}.get_product', return_value=ZA266):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        si.refresh_from_db(); self.supplier_order.refresh_from_db()
        self.assertEqual(si.status, 'added')
        self.assertIn('AGOTADO en Modaverse', si.notes)
        self.assertEqual(self.supplier_order.status, 'partial')
        self.assertIn('ZA-266', self.supplier_order.cart_script)

    def test_stock_insuficiente_tambien_deja_partial(self):
        prod = self._producto('ZA-266', 'ZA-266', '2082402530293186562')
        self._item(prod, '', 5)
        with patch(f'{MOD}.get_product', return_value=dict(ZA266, stockNum=3)):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.supplier_order.refresh_from_db()
        self.assertEqual(self.supplier_order.status, 'partial')

    def test_despublicado_con_stock_sigue_done(self):
        """El aviso de despublicado se conserva, pero solo la falta de stock baja a partial."""
        prod = self._producto('ZA-266', 'ZA-266', '2082402530293186562')
        si = self._item(prod, '', 1)
        with patch(f'{MOD}.get_product', return_value=dict(ZA266, ynLaunch='0', stockNum=10)):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        si.refresh_from_db(); self.supplier_order.refresh_from_db()
        self.assertIn('despublicado', si.notes)
        self.assertEqual(self.supplier_order.status, 'done')

    # ── Reintentar fallidos ────────────────────────────────────────────────────
    # El panel devuelve a 'pending' solo los variant_not_found y relanza el
    # comando. Los 'added' de la corrida anterior tienen que seguir en el script:
    # pegarlo reemplaza shopCarList entero en modaverse.

    def _ya_agregado(self, producto, variant, qty, notas=''):
        si = self._item(producto, variant, qty)
        si.status, si.notes = 'added', notas
        si.save(update_fields=['status', 'notes'])
        return si

    def test_reintento_conserva_en_el_script_los_ya_agregados(self):
        fog = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        za  = self._producto('ZA-156', 'ZA-156', '2062727000623153154')
        self._ya_agregado(fog, 'Talla M', 2)
        nuevo = self._item(za, '', 1)
        api = {'PR20260325154032003456': FOG0124, '2062727000623153154': ZA156}
        with patch(f'{MOD}.get_product', side_effect=lambda pid, client=None: api[pid]):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.supplier_order.refresh_from_db(); nuevo.refresh_from_db()
        arr = self._entradas_del_script(self.supplier_order.cart_script)
        self.assertEqual({e['productName']: e['num'] for e in arr}, {'FOG0124': 2, 'ZA-156': 1})
        self.assertEqual(nuevo.status, 'added')

    def test_reintento_suma_la_misma_variante_ya_agregada(self):
        fog = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        self._ya_agregado(fog, 'Talla M', 2)
        self._item(fog, 'Talla M', 3)
        with patch(f'{MOD}.get_product', return_value=FOG0124):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.supplier_order.refresh_from_db()
        arr = self._entradas_del_script(self.supplier_order.cart_script)
        self.assertEqual([e['num'] for e in arr], [5])

    def test_reintento_sin_pendientes_regenera_el_script_y_no_deja_pending(self):
        """La vista deja el pedido en 'pending' antes de lanzar el comando."""
        fog = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        self._ya_agregado(fog, 'Talla M', 1)
        self.supplier_order.status, self.supplier_order.cart_script = 'pending', ''
        self.supplier_order.save()
        with patch(f'{MOD}.get_product', return_value=FOG0124):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.supplier_order.refresh_from_db()
        self.assertEqual(self.supplier_order.status, 'done')
        self.assertIn('FOG0124', self.supplier_order.cart_script)

    def test_reintento_con_agotado_ya_agregado_sigue_partial(self):
        za = self._producto('ZA-266', 'ZA-266', '2082402530293186562')
        self._ya_agregado(za, '', 1, notas='⚠ AGOTADO en Modaverse (stock -1)')
        with patch(f'{MOD}.get_product', return_value=ZA266):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        self.supplier_order.refresh_from_db()
        self.assertEqual(self.supplier_order.status, 'partial')

    def test_ya_agregado_que_no_se_puede_armar_conserva_su_estado_y_deja_partial(self):
        """Marcado a mano en el panel: no se le pisa el estado, pero no está en el script."""
        fog = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        za  = self._producto('ZA-156', 'ZA-156', '2062727000623153154')
        a_mano = self._ya_agregado(fog, 'Talla XXXL', 1, notas='Agregado manualmente')
        self._item(za, '', 1)
        # Publicado y con stock: el único motivo para 'partial' es el renglón fuera del script.
        api = {'PR20260325154032003456': FOG0124,
               '2062727000623153154': dict(ZA156, ynLaunch='1', stockNum=50)}
        with patch(f'{MOD}.get_product', side_effect=lambda pid, client=None: api[pid]):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        a_mano.refresh_from_db(); self.supplier_order.refresh_from_db()
        self.assertEqual((a_mano.status, a_mano.notes), ('added', 'Agregado manualmente'))
        self.assertNotIn('FOG0124', self.supplier_order.cart_script)
        self.assertEqual(self.supplier_order.status, 'partial')

    def test_ya_agregado_a_mano_que_si_se_arma_conserva_su_nota(self):
        fog = self._producto('FOG0124', 'FOG0124', 'PR20260325154032003456')
        a_mano = self._ya_agregado(fog, 'Talla M', 1, notas='Agregado manualmente')
        with patch(f'{MOD}.get_product', return_value=FOG0124):
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        a_mano.refresh_from_db(); self.supplier_order.refresh_from_db()
        self.assertEqual(a_mano.notes, 'Agregado manualmente')
        self.assertIn('FOG0124', self.supplier_order.cart_script)
        self.assertEqual(self.supplier_order.status, 'done')

    def test_sin_nada_que_armar_restaura_el_estado(self):
        """Todo no_url: no hay script posible, pero el pedido no puede quedar 'pending'."""
        prod = self._producto('SINURL', 'Sin proveedor', '')
        si = self._item(prod, '', 1)
        si.status = 'no_url'; si.save(update_fields=['status'])
        with patch(f'{MOD}.get_product') as mock_get:
            call_command('sync_modaverse_order', self.order.pk, stdout=StringIO())
        mock_get.assert_not_called()
        self.supplier_order.refresh_from_db()
        self.assertEqual(self.supplier_order.status, 'done')

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
