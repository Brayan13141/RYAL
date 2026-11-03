from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Product, ProductImage, SizeGroup, VolumeTier
from orders.models import Order


class OrderEditEndpointsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ropa = Category.objects.create(name='Ropa', slug='ropa')
        cls.tallas = SizeGroup.objects.create(name='Ropa', sizes=['S', 'M', 'S', 'L'])
        cls.playera = Product.objects.create(
            sku='P1', name='Playera Azul', category=cls.ropa, base_price=100,
            size_group=cls.tallas, variant_colors=['Rojo'])
        cls.gorra = Product.objects.create(
            sku='G1', name='Gorra lisa', category=cls.ropa, base_price=50)
        cls.inactivo = Product.objects.create(
            sku='X1', name='Gorra vieja', category=cls.ropa, base_price=50, is_active=False)
        cls.pronto = Product.objects.create(
            sku='X2', name='Gorra futura', category=cls.ropa, base_price=50, status='coming_soon')
        cls.staff = User.objects.create_user('staff_edit', password='x', is_staff=True)

    def setUp(self):
        self.client.login(username='staff_edit', password='x')
        self.order = Order.objects.create(
            order_code='ED-1', customer_name='Ana', customer_phone='5512345678',
            customer_email='ana@example.com',
        )
        self.item = self.order.items.create(
            product=self.gorra, quantity=1, price_snapshot=Decimal('150'),
            sku_snapshot='G1', name_snapshot='Gorra lisa',
        )

    def _url(self, name, *args):
        return reverse(f'panel:{name}', args=[self.order.pk, *args])

    def test_no_staff_redirige_al_login(self):
        self.client.logout()
        resp = self.client.post(self._url('order_item_add'), {'product_id': self.gorra.pk, 'qty': 1})
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(self._url('order_product_search'), {'q': 'gorra'})
        self.assertEqual(resp.status_code, 302)

    def test_agregar_ok_devuelve_totales(self):
        resp = self.client.post(self._url('order_item_add'), {
            'product_id': self.gorra.pk, 'qty': 2, 'precio': '200'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['total'], 600.0)  # suma al renglón existente (3 x 200)
        self.assertEqual(data['balance_due'], 600.0)
        self.assertFalse(data['is_paid'])

    def test_post_tras_cambio_de_estado_rechaza(self):
        Order.objects.filter(pk=self.order.pk).update(status='in_preparation')
        resp = self.client.post(self._url('order_item_add'), {'product_id': self.gorra.pk, 'qty': 1})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])
        self.assertEqual(resp.json()['error'], 'Solo se pueden editar pedidos Pendientes o Confirmados.')

    def test_precio_invalido_400(self):
        for malo in ('abc', '-5', '1,200', '1.234', '1000000'):
            resp = self.client.post(self._url('order_item_add'), {
                'product_id': self.gorra.pk, 'qty': 1, 'precio': malo})
            self.assertEqual(resp.status_code, 400, malo)
            self.assertFalse(resp.json()['ok'])
        resp = self.client.post(self._url('order_item_add'), {
            'product_id': self.gorra.pk, 'qty': 1, 'precio': 'abc'})
        self.assertEqual(resp.json()['error'], 'Precio inválido.')
        resp = self.client.post(self._url('order_item_qty', self.item.pk), {'qty': 'x'})
        self.assertEqual(resp.json()['error'], 'Cantidad inválida.')
        # Vacío usa el sugerido
        resp = self.client.post(self._url('order_item_add'), {
            'product_id': self.gorra.pk, 'qty': 1, 'precio': ''})
        self.assertEqual(resp.status_code, 200)

    def test_item_de_otro_pedido_404(self):
        otro = Order.objects.create(order_code='ED-2', customer_name='B', customer_phone='1',
                                    customer_email='b@example.com')
        ajeno = otro.items.create(product=self.gorra, quantity=1, price_snapshot=Decimal('10'),
                                  sku_snapshot='G1', name_snapshot='Gorra lisa')
        self.assertEqual(self.client.post(self._url('order_item_qty', ajeno.pk), {'qty': 2}).status_code, 404)
        self.assertEqual(self.client.post(self._url('order_item_remove', ajeno.pk)).status_code, 404)

    def test_quitar_y_cantidad_ok(self):
        resp = self.client.post(self._url('order_item_qty', self.item.pk), {'qty': 3, 'precio': '100'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total'], 300.0)
        # El único renglón no se puede quitar (regla del servicio): hace falta otro.
        resp = self.client.post(self._url('order_item_remove', self.item.pk))
        self.assertEqual(resp.status_code, 400)
        self.order.items.create(product=self.playera, quantity=1, price_snapshot=Decimal('10'),
                                sku_snapshot='P1', name_snapshot='Playera Azul')
        resp = self.client.post(self._url('order_item_remove', self.item.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(resp.json()['total'], 10.0)
        self.assertEqual(self.order.items.count(), 1)

    def test_buscar_excluye_inactivos_y_coming_soon(self):
        resp = self.client.get(self._url('order_product_search'), {'q': 'gorra'})
        self.assertEqual([r['sku'] for r in resp.json()], ['G1'])
        self.assertEqual(self.client.get(self._url('order_product_search'), {'q': 'g'}).json(), [])
        row = self.client.get(self._url('order_product_search'), {'q': 'P1'}).json()[0]
        self.assertEqual(set(row), {'id', 'name', 'sku', 'price', 'thumb'})

    def test_opciones_tallas_colores_y_sugerido(self):
        url = reverse('panel:order_product_options', args=[self.order.pk, self.playera.pk])
        data = self.client.get(url, {'qty': 2}).json()
        self.assertEqual(data['sizes'], ['S', 'M', 'L'])
        self.assertEqual(data['colors'], ['Rojo'])
        self.assertEqual(data['colorways'], [])
        self.assertEqual(data['sugerido'], 200.0)
        self.assertEqual(data['costo'], 100.0)
        self.assertEqual(data['min_qty'], 1)

    def test_opciones_con_renglon_existente_sugiere_precio_combinado(self):
        # Gorra: 150 sin tier; con 5+ piezas, 120. El pedido ya trae 1 a $150.
        VolumeTier.objects.create(category=self.ropa, min_qty=5, discount_amount=30)
        url = reverse('panel:order_product_options', args=[self.order.pk, self.gorra.pk])
        data = self.client.get(url, {'qty': 4}).json()
        self.assertEqual(data['existente'], {'item_pk': self.item.pk, 'quantity': 1, 'price': 150.0})
        self.assertEqual(data['sugerido'], 120.0)   # tier de 1 + 4 = 5
        # 1 + 3 = 4 todavía no alcanza el tier.
        self.assertEqual(self.client.get(url, {'qty': 3}).json()['sugerido'], 150.0)
        # Sin renglón existente, el sugerido es el de la cantidad sola.
        url = reverse('panel:order_product_options', args=[self.order.pk, self.playera.pk])
        data = self.client.get(url, {'qty': 4, 'size_name': 'M', 'color': 'Rojo'}).json()
        self.assertIsNone(data['existente'])
        self.assertEqual(data['sugerido'], 200.0)

    def test_opciones_existente_distingue_la_variante(self):
        VolumeTier.objects.create(category=self.ropa, min_qty=5, discount_amount=30)
        renglon = self.order.items.create(
            product=self.playera, quantity=3, price_snapshot=Decimal('190'),
            sku_snapshot='P1', name_snapshot='Playera Azul', variant_snapshot='Talla M / Rojo')
        url = reverse('panel:order_product_options', args=[self.order.pk, self.playera.pk])
        data = self.client.get(url, {'qty': 2, 'size_name': 'M', 'color': 'Rojo'}).json()
        self.assertEqual(data['existente'], {'item_pk': renglon.pk, 'quantity': 3, 'price': 190.0})
        self.assertEqual(data['sugerido'], 170.0)
        data = self.client.get(url, {'qty': 2, 'size_name': 'L', 'color': 'Rojo'}).json()
        self.assertIsNone(data['existente'])
        self.assertEqual(data['sugerido'], 200.0)

    def test_opciones_para_un_renglon_no_suma_existente(self):
        # La fila que cambia su propia cantidad pide el precio de esa cantidad, sin
        # combinarse consigo misma.
        VolumeTier.objects.create(category=self.ropa, min_qty=5, discount_amount=30)
        url = reverse('panel:order_product_options', args=[self.order.pk, self.gorra.pk])
        data = self.client.get(url, {'qty': 4, 'item_pk': self.item.pk}).json()
        self.assertIsNone(data['existente'])
        self.assertEqual(data['sugerido'], 150.0)
        self.assertEqual(self.client.get(url, {'qty': 5, 'item_pk': self.item.pk}).json()['sugerido'], 120.0)

    def test_cantidad_mayor_al_tope_400(self):
        resp = self.client.post(self._url('order_item_add'), {'product_id': self.gorra.pk, 'qty': 100000})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error'], 'Cantidad inválida.')
        resp = self.client.post(self._url('order_item_qty', self.item.pk), {'qty': 100000})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error'], 'Cantidad inválida.')
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 1)
        resp = self.client.post(self._url('order_item_qty', self.item.pk), {'qty': 99999})
        self.assertEqual(resp.status_code, 200)

    def test_opciones_404_si_inactivo_o_coming_soon(self):
        for p in (self.inactivo, self.pronto):
            url = reverse('panel:order_product_options', args=[self.order.pk, p.pk])
            self.assertEqual(self.client.get(url).status_code, 404)


class OrderDetailEdicionUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ropa = Category.objects.create(name='Ropa', slug='ropa')
        cls.gorra = Product.objects.create(
            sku='G1', name='Gorra lisa', category=cls.ropa, base_price=50)
        cls.staff = User.objects.create_user('staff_ui', password='x', is_staff=True)

    def setUp(self):
        self.client.login(username='staff_ui', password='x')
        self.order = Order.objects.create(
            order_code='UI-1', customer_name='Ana', customer_phone='5512345678',
            customer_email='ana@example.com',
        )
        self.item = self.order.items.create(
            product=self.gorra, quantity=1, price_snapshot=Decimal('150'),
            sku_snapshot='G1', name_snapshot='Gorra lisa',
        )

    def _get(self):
        return self.client.get(reverse('panel:order_detail', args=[self.order.pk]))

    def test_detalle_muestra_controles_si_editable(self):
        resp = self._get()
        self.assertTrue(resp.context['editable'])
        self.assertIsNone(resp.context['motivo_no_editable'])
        self.assertContains(resp, reverse('panel:order_item_add', args=[self.order.pk]))

    def test_detalle_oculta_controles_en_preparacion(self):
        Order.objects.filter(pk=self.order.pk).update(status='in_preparation')
        resp = self._get()
        self.assertFalse(resp.context['editable'])
        self.assertNotContains(resp, reverse('panel:order_item_add', args=[self.order.pk]))
        # En preparación no se explica nada: el estado ya lo dice.
        self.assertNotContains(resp, 'Solo se pueden editar pedidos')

    def test_detalle_muestra_motivo_si_proveedor_lanzado(self):
        from orders.models import SupplierOrder
        SupplierOrder.objects.create(order=self.order, run_started_at=timezone.now())
        resp = self._get()
        self.assertFalse(resp.context['editable'])
        self.assertContains(resp, 'El pedido al proveedor ya se lanzó')
        self.assertNotContains(resp, reverse('panel:order_item_add', args=[self.order.pk]))

    def test_detalle_muestra_historial(self):
        self.order.item_changes.create(
            usuario=self.staff, accion='cantidad', descripcion='Gorra lisa: 1 a 3',
            cantidad_antes=1, cantidad_despues=3,
            precio_antes=Decimal('150'), precio_despues=Decimal('140'))
        resp = self._get()
        self.assertEqual(len(resp.context['item_changes']), 1)
        self.assertContains(resp, 'Historial de cambios')
        self.assertContains(resp, 'Gorra lisa: 1 a 3')

    def test_detalle_sin_historial_no_muestra_tarjeta(self):
        self.assertNotContains(self._get(), 'Historial de cambios')

    def test_detalle_saldo_a_favor(self):
        self.order.payments.create(monto=Decimal('400'), metodo_pago='efectivo', fecha=timezone.localdate())
        resp = self._get()
        self.assertTrue(resp.context['order'].balance_due < 0)
        self.assertContains(resp, '>Saldo a favor</span>')
        self.assertNotContains(resp, '>Saldo pendiente</span>')
        self.assertContains(resp, '$250 MXN')  # monto en positivo
