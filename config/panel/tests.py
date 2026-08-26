from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, Category
from negocio.models import Cliente, Pedido, Pago, Gasto
from orders.models import Order, OrderItem, OrderPayment


class ResumenGlobalViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_rg', password='pass', is_staff=True
        )
        self.client.login(username='staff_rg', password='pass')

    def test_accesible_staff(self):
        res = self.client.get('/panel/resumen-global/')
        self.assertEqual(res.status_code, 200)

    def test_redirige_anonimo(self):
        self.client.logout()
        res = self.client.get('/panel/resumen-global/')
        self.assertEqual(res.status_code, 302)

    def test_vendido_negocio_neto_de_descuentos(self):
        """vendido_negocio resta descuento_aplicado — mismo criterio neto que
        el dashboard. Antes reportaba bruto y las cifras no cuadraban."""
        cliente = Cliente.objects.create(nombre='Z', telefono='5550007777')
        Pedido.objects.create(
            cliente=cliente, descripcion='Con descuento',
            costo_producto=Decimal('400'), precio_venta=Decimal('1000'),
            descuento_aplicado=Decimal('100'), estado=Pedido.PAGADO,
        )
        res = self.client.get('/panel/resumen-global/')
        self.assertEqual(res.context['vendido_negocio'], 900)
        self.assertEqual(res.context['ganancia_negocio'], 500)
        self.assertEqual(res.context['ingresos_total'], 900)

    def test_periodo_default_es_mes_actual(self):
        hoy = date.today()
        res = self.client.get('/panel/resumen-global/')
        self.assertEqual(res.context['mes'], f"{hoy.year}-{hoy.month:02d}")

    def test_periodo_todo(self):
        res = self.client.get('/panel/resumen-global/?mes=todo')
        self.assertEqual(res.context['periodo_label'], 'Todo el tiempo')

    def test_periodo_mes_especifico(self):
        res = self.client.get('/panel/resumen-global/?mes=2026-06')
        self.assertContains(res, 'Jun 2026')


class ResumenGlobalTotalesTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_rg2', password='pass', is_staff=True
        )
        self.client.login(username='staff_rg2', password='pass')

        cat = Category.objects.create(name='Gorras', slug='gorras')
        product = Product.objects.create(
            name='Gorra X', sku='GX-1', category=cat,
            base_price=Decimal('100'), is_active=True,
        )
        order = Order.objects.create(
            customer_name='Cliente Tienda', customer_phone='5551112222',
            status='confirmed', is_paid=True,
        )
        OrderItem.objects.create(
            order=order, product=product, quantity=1,
            price_snapshot=Decimal('300'), cost_snapshot=Decimal('100'),
            sku_snapshot='GX-1', name_snapshot='Gorra X',
        )

        cliente = Cliente.objects.create(nombre='Cliente Negocio', telefono='5553334444')
        Pedido.objects.create(
            cliente=cliente, descripcion='Venta mostrador',
            costo_producto=Decimal('50'), precio_venta=Decimal('150'),
            estado=Pedido.PAGADO,
        )

    def test_totales_combinados_suman_ambas_fuentes(self):
        res = self.client.get('/panel/resumen-global/?mes=todo')
        self.assertEqual(res.context['rev_tienda'], 300)
        self.assertEqual(res.context['gan_tienda'], 200)
        self.assertEqual(res.context['vendido_negocio'], 150)
        self.assertEqual(res.context['ganancia_negocio'], 100)
        self.assertEqual(res.context['ingresos_total'], 450)
        self.assertEqual(res.context['ganancia_total'], 300)


class CategoryOverridePricingFormTests(TestCase):
    """El panel de staff permite configurar base_price_override y
    profit_margin_override al editar una subcategoría."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_cat', password='pass', is_staff=True
        )
        self.client.login(username='staff_cat', password='pass')
        self.root = Category.objects.create(
            name='Joyería Panel', slug='joyeria-panel-test',
            shipping_cost=Decimal('50'), profit_margin=Decimal('100'),
        )
        self.sub = Category.objects.create(
            name='Anillos Panel', slug='anillos-panel-test', parent=self.root,
        )

    def test_guarda_overrides_al_editar_subcategoria(self):
        res = self.client.post(f'/panel/categorias/{self.sub.pk}/editar/', {
            'name': self.sub.name,
            'slug': self.sub.slug,
            'parent': self.root.pk,
            'shipping_cost': '0',
            'profit_margin': '100',
            'base_price_override': '300',
            'profit_margin_override': '150',
            'min_order_qty': '1',
            'min_qty_per_item': '0',
            'display_order': '0',
            'is_active': 'on',
        })
        self.assertEqual(res.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.base_price_override, Decimal('300'))
        self.assertEqual(self.sub.profit_margin_override, Decimal('150'))

    def test_campo_vacio_guarda_none_no_cero(self):
        """Dejar el campo vacío debe significar 'heredar', no Decimal('0')."""
        self.sub.base_price_override = Decimal('300')
        self.sub.profit_margin_override = Decimal('150')
        self.sub.save()

        res = self.client.post(f'/panel/categorias/{self.sub.pk}/editar/', {
            'name': self.sub.name,
            'slug': self.sub.slug,
            'parent': self.root.pk,
            'shipping_cost': '0',
            'profit_margin': '100',
            'base_price_override': '',
            'profit_margin_override': '',
            'min_order_qty': '1',
            'min_qty_per_item': '0',
            'display_order': '0',
            'is_active': 'on',
        })
        self.assertEqual(res.status_code, 302)
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.base_price_override)
        self.assertIsNone(self.sub.profit_margin_override)

    def test_form_de_subcategoria_muestra_los_campos_de_override(self):
        res = self.client.get(f'/panel/categorias/{self.sub.pk}/editar/')
        self.assertContains(res, 'base_price_override')
        self.assertContains(res, 'profit_margin_override')

    def test_form_de_raiz_no_muestra_los_campos_de_override(self):
        res = self.client.get(f'/panel/categorias/{self.root.pk}/editar/')
        self.assertNotContains(res, 'name="base_price_override"')
        self.assertNotContains(res, 'name="profit_margin_override"')

    def test_form_de_subcategoria_no_muestra_costo_envio_ni_margen_base(self):
        """shipping_cost/profit_margin de una subcategoria nunca se leen (ver
        Product._root_category) -- mostrarlos confunde, solo van los overrides."""
        res = self.client.get(f'/panel/categorias/{self.sub.pk}/editar/')
        self.assertNotContains(res, 'name="shipping_cost"')
        self.assertNotContains(res, 'name="profit_margin"')

    def test_editar_subcategoria_no_le_quita_el_padre(self):
        """category_form.html no tiene campo parent -- category_edit no debe
        resetear parent_id a None al guardar (bug: convertia la sub en raiz)."""
        res = self.client.post(f'/panel/categorias/{self.sub.pk}/editar/', {
            'name': self.sub.name,
            'slug': self.sub.slug,
            'shipping_cost': '0',
            'profit_margin': '100',
            'min_order_qty': '1',
            'min_qty_per_item': '0',
            'display_order': '0',
            'is_active': 'on',
        })
        self.assertEqual(res.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.parent_id, self.root.pk)


class DashboardAdelantosSaldoPendienteTests(TestCase):
    """El dashboard debe mostrar adelantos y saldo pendiente por separado,
    para checkout web (OrderPayment) y para WhatsApp/tienda (Pago parcial)."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_dash', password='pass', is_staff=True
        )
        self.client.login(username='staff_dash', password='pass')

        order = Order.objects.create(
            customer_name='Victor', customer_phone='5550001111',
            status='in_preparation', is_paid=False,
        )
        OrderItem.objects.create(
            order=order, product=None, quantity=1,
            price_snapshot=Decimal('800'), sku_snapshot='X', name_snapshot='X',
        )
        OrderPayment.objects.create(
            order=order, fecha=date.today(), monto=Decimal('300'),
            metodo_pago='efectivo',
        )
        # total = 800, pagado = 300 -> balance_due = 500

        cliente = Cliente.objects.create(nombre='Cliente WA', telefono='5559998888')
        pedido = Pedido.objects.create(
            cliente=cliente, descripcion='Venta WA',
            costo_producto=Decimal('300'), precio_venta=Decimal('800'),
            estado=Pedido.PENDIENTE,
        )
        Pago.objects.create(
            pedido=pedido, fecha=date.today(), monto=Decimal('300'),
            metodo_pago=Pago.EFECTIVO,
        )
        # total_a_cobrar = 800, pagado = 300 -> balance_pendiente = 500

    def test_dashboard_separa_adelantos_y_saldo_pendiente(self):
        res = self.client.get('/panel/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['adelantos_web'], 300)
        self.assertEqual(res.context['saldo_pendiente_web'], 500)
        self.assertEqual(res.context['adelantos_negocio'], 300)
        self.assertEqual(res.context['saldo_negocio_pendiente'], 500)


class OrdersListAdelantoBadgeTests(TestCase):
    """La lista de pedidos debe mostrar 'Adelanto' cuando hay un pago parcial
    registrado en OrderPayment, y '—' cuando el pedido no tiene ningún pago."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_orders_list', password='pass', is_staff=True
        )
        self.client.login(username='staff_orders_list', password='pass')

        self.order_con_adelanto = Order.objects.create(
            order_code=f'BADGE-A-{uuid4().hex[:10]}',
            customer_name='Con Adelanto', customer_phone='5550002222',
            status='in_preparation', is_paid=False,
        )
        OrderItem.objects.create(
            order=self.order_con_adelanto, product=None, quantity=1,
            price_snapshot=Decimal('800'), sku_snapshot='Y', name_snapshot='Y',
        )
        OrderPayment.objects.create(
            order=self.order_con_adelanto, fecha=date.today(), monto=Decimal('200'),
            metodo_pago='efectivo',
        )

        self.order_sin_pago = Order.objects.create(
            order_code=f'BADGE-B-{uuid4().hex[:10]}',
            customer_name='Sin Pago', customer_phone='5550003333',
            status='in_preparation', is_paid=False,
        )
        OrderItem.objects.create(
            order=self.order_sin_pago, product=None, quantity=1,
            price_snapshot=Decimal('800'), sku_snapshot='Z', name_snapshot='Z',
        )

    def test_badge_adelanto_y_sin_pago_en_lista(self):
        res = self.client.get(reverse('panel:orders_list'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        self.assertIn('Adelanto', content)
        self.assertIn('—', content)


class ProductsListFilterPersistenceTests(TestCase):
    """Al editar/crear/eliminar un producto desde una lista filtrada debe
    volver a la misma vista filtrada, en vez de a la lista sin filtros."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_prod_filter', password='pass', is_staff=True
        )
        self.client.login(username='staff_prod_filter', password='pass')
        self.cat = Category.objects.create(name='Gorras Filter', slug='gorras-filter-test')
        self.product = Product.objects.create(
            name='Gorra Filter', sku='GF-FILTER-1', category=self.cat,
            base_price=Decimal('100'), is_active=True,
        )
        self.qs = 'q=gorra&cat=gorras-filter-test'

    def test_form_de_edicion_incluye_filtros_en_campo_oculto(self):
        res = self.client.get(f'/panel/productos/{self.product.pk}/editar/?{self.qs}')
        self.assertContains(res, 'name="_return_qs" value="q=gorra&amp;cat=gorras-filter-test"')

    def test_editar_producto_preserva_filtros_al_guardar(self):
        res = self.client.post(
            f'/panel/productos/{self.product.pk}/editar/?{self.qs}', {
                '_return_qs': self.qs,
                'sku': self.product.sku,
                'name': self.product.name,
                'base_price': '150',
                'category': self.cat.pk,
                'status': 'available',
                'min_order_qty': '1',
                'is_active': 'on',
            },
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, f'/panel/productos/?{self.qs}')

    def test_crear_producto_preserva_filtros(self):
        res = self.client.post(
            f'/panel/productos/nuevo/?{self.qs}', {
                '_return_qs': self.qs,
                'sku': 'GF-NEW-1',
                'name': 'Nueva Gorra',
                'base_price': '120',
                'category': self.cat.pk,
                'status': 'available',
                'min_order_qty': '1',
                'is_active': 'on',
            },
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, f'/panel/productos/?{self.qs}')

    def test_eliminar_producto_preserva_filtros(self):
        res = self.client.post(f'/panel/productos/{self.product.pk}/eliminar/?{self.qs}')
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, f'/panel/productos/?{self.qs}')

    def test_sin_filtros_redirige_a_lista_plana(self):
        res = self.client.post(f'/panel/productos/{self.product.pk}/eliminar/')
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, '/panel/productos/')


class OrderDescuentoApplyCapTest(TestCase):
    """El descuento aplicado desde el panel se capea al subtotal del pedido."""

    def setUp(self):
        from catalog.models import CodigoDescuento
        self.staff = User.objects.create_user(
            username='staff_desc', password='pass', is_staff=True
        )
        self.client.login(username='staff_desc', password='pass')
        cat = Category.objects.create(name='Gorras Panel', slug='gorras-panel')
        product = Product.objects.create(
            sku='RYL-PNL-1', name='Gorra Panel', category=cat,
            base_price=Decimal('100'),
        )
        self.order = Order.objects.create(
            order_code='TEST-DESC-1', customer_name='Ana', customer_phone='5512345678',
        )
        self.order.items.create(
            product=product, quantity=1, price_snapshot=Decimal('300'),
            sku_snapshot=product.sku, name_snapshot=product.name,
        )
        self.code = CodigoDescuento.objects.create(
            codigo='MEGAPANEL', descuento=Decimal('10000'), tipo_descuento='fijo',
        )

    def test_descuento_se_capea_al_subtotal(self):
        res = self.client.post(f'/panel/pedidos/{self.order.pk}/descuento/',
                               {'codigo': 'MEGAPANEL'})
        data = res.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['descuento'], 300.0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.descuento_aplicado, Decimal('300'))
        self.assertEqual(self.order.total, Decimal('0'))

    def test_codigo_agotado_devuelve_error(self):
        self.code.usos_max = 1
        self.code.usos_actuales = 1
        self.code.save()
        res = self.client.post(f'/panel/pedidos/{self.order.pk}/descuento/',
                               {'codigo': 'MEGAPANEL'})
        data = res.json()
        self.assertFalse(data['ok'])
        self.order.refresh_from_db()
        self.assertEqual(self.order.descuento_aplicado, Decimal('0'))
        self.code.refresh_from_db()
        self.assertEqual(self.code.usos_actuales, 1)


class DashboardFlujoMesTests(TestCase):
    """El card 'Ingresos − Gastos' muestra flujo de caja bruto: lo facturado
    menos los gastos operativos, SIN restar el costo de la mercancía (eso es
    lo que hace el card 'Balance (gan − gastos)', que es otra métrica)."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_flujo', password='pass', is_staff=True
        )
        self.client.login(username='staff_flujo', password='pass')

    def _order(self, precio, cantidad=1, status='in_preparation'):
        # order_code no tiene default a nivel de modelo (se genera en
        # orders/views.py al crear desde el checkout); aquí hay que asignarlo
        # explícito y único o dos Order.objects.create() en el mismo test
        # chocan contra el UNIQUE constraint (ambos caen en '').
        order = Order.objects.create(
            order_code=f'FLUJO-{uuid4().hex[:10]}',
            customer_name='Flujo', customer_phone='5550002222',
            status=status, is_paid=True,
        )
        OrderItem.objects.create(
            order=order, product=None, quantity=cantidad,
            price_snapshot=Decimal(precio), cost_snapshot=Decimal('100'),
            sku_snapshot='F', name_snapshot='F',
        )
        return order

    def test_flujo_mes_es_ingresos_menos_gastos(self):
        self._order('800')
        Gasto.objects.create(
            fecha=date.today(), descripcion='Renta', monto=Decimal('500'),
        )
        res = self.client.get('/panel/')
        self.assertEqual(res.status_code, 200)
        # ingresos 800 - gastos 500 = 300
        self.assertEqual(res.context['flujo_mes'], 300)
        self.assertEqual(
            res.context['flujo_mes'],
            res.context['rev_mes'] - res.context['gastos_mes'],
        )

    def test_flujo_mes_negativo_cuando_gastos_superan_ingresos(self):
        self._order('800')
        Gasto.objects.create(
            fecha=date.today(), descripcion='Compra proveedor',
            monto=Decimal('2000'), categoria=Gasto.COMPRA_PROVEEDOR,
        )
        res = self.client.get('/panel/')
        # 800 - 2000 = -1200
        self.assertEqual(res.context['flujo_mes'], -1200)

    def test_flujo_mes_ignora_ordenes_canceladas(self):
        self._order('800')
        self._order('5000', status='cancelled')
        Gasto.objects.create(
            fecha=date.today(), descripcion='Envío', monto=Decimal('500'),
        )
        res = self.client.get('/panel/')
        # la orden cancelada no suma: sigue siendo 800 - 500 = 300
        self.assertEqual(res.context['flujo_mes'], 300)

    def test_flujo_mes_difiere_de_balance_mes(self):
        """flujo_mes NO resta costo de mercancía; balance_mes sí. Si este test
        empieza a fallar por igualdad, alguien colapsó las dos métricas."""
        self._order('800')
        Gasto.objects.create(
            fecha=date.today(), descripcion='Renta', monto=Decimal('500'),
        )
        res = self.client.get('/panel/')
        self.assertNotEqual(res.context['flujo_mes'], res.context['balance_mes'])


class OrderPaymentEndpointTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from catalog.models import Category, Product
        from orders.models import Order
        self.staff = User.objects.create_user(
            username='staff_pay', password='pass', is_staff=True,
        )
        self.client.login(username='staff_pay', password='pass')
        cat = Category.objects.create(name="Gorras EP", slug="gorras-ep")
        product = Product.objects.create(
            sku="RYL-EP-1", name="Gorra EP", category=cat, base_price=Decimal("100"),
        )
        self.order = Order.objects.create(
            order_code="TEST-EP-1", customer_name="Ana", customer_phone="5512345678",
        )
        self.order.items.create(
            product=product, quantity=2, price_snapshot=Decimal("450"),
            sku_snapshot=product.sku, name_snapshot=product.name,
        )  # total = 900

    def test_add_parcial_no_liquida(self):
        url = reverse('panel:order_payment_add', args=[self.order.pk])
        res = self.client.post(url, {'monto': '300', 'metodo_pago': 'efectivo',
                                     'fecha': '2026-07-21', 'notas': ''})
        self.assertEqual(res.status_code, 200)
        d = res.json()
        self.assertTrue(d['ok'])
        self.assertEqual(d['balance_due'], 600.0)
        self.assertFalse(d['is_paid'])
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_paid)

    def test_add_completo_liquida(self):
        url = reverse('panel:order_payment_add', args=[self.order.pk])
        res = self.client.post(url, {'monto': '900', 'metodo_pago': 'transferencia',
                                     'fecha': '2026-07-21', 'notas': 'pago total'})
        d = res.json()
        self.assertEqual(d['balance_due'], 0.0)
        self.assertTrue(d['is_paid'])

    def test_add_monto_invalido_400(self):
        url = reverse('panel:order_payment_add', args=[self.order.pk])
        res = self.client.post(url, {'monto': '0', 'metodo_pago': 'efectivo',
                                     'fecha': '2026-07-21'})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()['ok'])

    def test_liquidar_crea_pago_del_saldo(self):
        from orders.models import OrderPayment
        OrderPayment.objects.create(order=self.order, fecha='2026-07-21',
                                    monto=Decimal('200'), metodo_pago='efectivo')
        url = reverse('panel:order_liquidar', args=[self.order.pk])
        res = self.client.post(url)
        d = res.json()
        self.assertTrue(d['ok'])
        self.assertEqual(d['balance_due'], 0.0)
        self.assertTrue(d['is_paid'])
        self.assertEqual(Decimal(str(d['payment']['monto'])), Decimal('700'))
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_paid)

    def test_delete_reabre_pedido(self):
        from orders.models import OrderPayment
        pago = OrderPayment.objects.create(order=self.order, fecha='2026-07-21',
                                           monto=Decimal('900'), metodo_pago='efectivo')
        self.order.recalc_paid()
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_paid)
        url = reverse('panel:order_payment_delete', args=[pago.pk])
        res = self.client.post(url)
        d = res.json()
        self.assertTrue(d['ok'])
        self.assertEqual(d['balance_due'], 900.0)
        self.assertFalse(d['is_paid'])


class OrderDetailRenderTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from catalog.models import Category, Product
        from orders.models import Order
        User.objects.create_user(username='staff_rp', password='pass', is_staff=True)
        self.client.login(username='staff_rp', password='pass')
        cat = Category.objects.create(name="Gorras RP", slug="gorras-rp")
        product = Product.objects.create(
            sku="RYL-RP-1", name="Gorra RP", category=cat, base_price=Decimal("100"),
        )
        self.order = Order.objects.create(
            order_code="TEST-RP-1", customer_name="Ana", customer_phone="5512345678",
        )
        self.order.items.create(
            product=product, quantity=1, price_snapshot=Decimal("450"),
            sku_snapshot=product.sku, name_snapshot=product.name,
        )

    def test_detalle_muestra_pagos_y_liquidar(self):
        res = self.client.get(reverse('panel:order_detail', args=[self.order.pk]))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('Registrar pago', html)
        self.assertIn('id="pagosBody"', html)
        self.assertIn('id="btnLiquidar"', html)


@override_settings(RATELIMIT_ENABLE=False)
class DashboardReconocimientoTests(TestCase):
    """Reconocimiento unificado: el dashboard solo cuenta ventas PAGADAS,
    y las de negocio (WhatsApp/tienda) se atribuyen al mes por su `fecha`
    (cuándo se levantó el pedido), no por `created_at`."""

    def setUp(self):
        self.staff = User.objects.create_user('boss', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.cat = Category.objects.create(name='Gorras', shipping_cost=Decimal('280'),
                                            profit_margin=Decimal('100'))
        self.prod = Product.objects.create(sku='RYL-D-1', name='Gorra D',
                                            category=self.cat, base_price=Decimal('500'),
                                            is_active=True)

    def _web_order(self, *, paid, precio='880', costo='780'):
        o = Order.objects.create(order_code=f'D-{Order.objects.count()+1}',
                                 customer_name='C', customer_phone='1',
                                 status='confirmed', is_paid=paid)
        OrderItem.objects.create(order=o, product=self.prod, quantity=1,
                                 price_snapshot=Decimal(precio), cost_snapshot=Decimal(costo),
                                 sku_snapshot='RYL-D-1', name_snapshot='Gorra D')
        return o

    def test_web_no_pagado_no_cuenta_para_ganancia(self):
        self._web_order(paid=False)          # no pagado -> no cuenta
        res = self.client.get('/panel/')
        self.assertEqual(res.context['gan_mes'], 0)
        self.assertEqual(res.context['rev_mes'], 0)

    def test_web_pagado_si_cuenta(self):
        self._web_order(paid=True)           # 880 - 780 = 100 ganancia
        res = self.client.get('/panel/')
        self.assertEqual(res.context['gan_mes'], 100)
        self.assertEqual(res.context['rev_mes'], 880)

    def test_negocio_pendiente_no_cuenta(self):
        cli = Cliente.objects.create(nombre='N', telefono='9')
        Pedido.objects.create(cliente=cli, costo_producto=Decimal('780'),
                              precio_venta=Decimal('900'), estado=Pedido.PENDIENTE,
                              fecha=date.today())
        res = self.client.get('/panel/')
        self.assertEqual(res.context['gan_mes'], 0)

    def test_negocio_pagado_cuenta_por_fecha_del_pedido(self):
        cli = Cliente.objects.create(nombre='N', telefono='9')
        Pedido.objects.create(cliente=cli, costo_producto=Decimal('780'),
                              precio_venta=Decimal('900'), estado=Pedido.PAGADO,
                              fecha=date.today())
        res = self.client.get('/panel/')
        self.assertEqual(res.context['gan_mes'], 120)   # 900 - 780
        self.assertEqual(res.context['rev_mes'], 900)


@override_settings(RATELIMIT_ENABLE=False)
class DashboardCajaTests(TestCase):
    """'Total en caja' = saldo real acumulado: TODO lo cobrado histórico
    (OrderPayment web + Pago negocio, sin filtro de fecha) − TODOS los gastos.
    El dinero se arrastra mes a mes; incluye adelantos de pedidos no liquidados."""

    def setUp(self):
        self.staff = User.objects.create_user('boss2', password='x', is_staff=True)
        self.client.force_login(self.staff)

    def test_caja_es_cobrado_acumulado_menos_gastos(self):
        from datetime import timedelta
        from negocio.models import Gasto
        mes_pasado = date.today().replace(day=1) - timedelta(days=5)

        o = Order.objects.create(order_code='K-1', customer_name='C', customer_phone='1',
                                 status='pending', is_paid=False)
        # Adelanto del MES PASADO sobre un pedido web NO liquidado: NO cuenta.
        # Ese dinero es lo primero que se va en pagarle la mercancía al
        # proveedor, así que la caja no lo puede mostrar como disponible. Un
        # pedido web solo aporta su ganancia, y recién cuando está liquidado.
        OrderPayment.objects.create(order=o, fecha=mes_pasado, monto=Decimal('300'),
                                    metodo_pago='efectivo')
        cli = Cliente.objects.create(nombre='N', telefono='9')
        ped = Pedido.objects.create(cliente=cli, costo_producto=Decimal('0'),
                                    precio_venta=Decimal('500'), estado=Pedido.PENDIENTE,
                                    fecha=date.today())
        Pago.objects.create(pedido=ped, fecha=date.today(), monto=Decimal('200'),
                            metodo_pago='transferencia')
        Gasto.objects.create(fecha=date.today(), descripcion='Renta', monto=Decimal('100'))

        res = self.client.get('/panel/')
        self.assertEqual(res.context['caja_cobrado'], Decimal('200'))  # solo el del negocio
        self.assertEqual(res.context['caja_gastos'], Decimal('100'))
        self.assertEqual(res.context['caja_saldo'], Decimal('100'))    # 200 − 100


@override_settings(RATELIMIT_ENABLE=False)
class OrdersListFiltroFechaTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('s3', password='x', is_staff=True)
        self.client.force_login(self.staff)

    def _order(self):
        return Order.objects.create(order_code=f'F-{Order.objects.count()+1}',
                                    customer_name='C', customer_phone='1', status='pending')

    def test_filtra_por_created_at(self):
        from django.utils import timezone
        o_viejo = self._order()
        Order.objects.filter(pk=o_viejo.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=40))
        self._order()  # hoy
        hoy = timezone.now().date().isoformat()
        res = self.client.get(reverse('panel:orders_list') + f'?desde={hoy}')
        self.assertEqual(len(res.context['page_obj'].object_list), 1)


import json
import tempfile
from pathlib import Path

from panel.whatsapp import read_qr_state, get_instance, WHATSAPP_INSTANCES


class WhatsappStateReadingTests(TestCase):
    def test_read_qr_state_archivo_valido(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '.qr_state.json'
            p.write_text(json.dumps({
                'status': 'qr', 'qr': 'abc123', 'updated_at': '2026-08-03T10:00:00'
            }))
            data = read_qr_state(p)
            self.assertEqual(data['status'], 'qr')
            self.assertEqual(data['qr'], 'abc123')

    def test_read_qr_state_archivo_ausente(self):
        p = Path(tempfile.gettempdir()) / 'no_existe_este_archivo_qr.json'
        data = read_qr_state(p)
        self.assertEqual(data['status'], 'no_data')
        self.assertIsNone(data['qr'])

    def test_read_qr_state_json_corrupto(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '.qr_state.json'
            p.write_text('{esto no es json valido')
            data = read_qr_state(p)
            self.assertEqual(data['status'], 'no_data')

    def test_get_instance_conocida(self):
        inst = get_instance(WHATSAPP_INSTANCES[0]['key'])
        self.assertIsNotNone(inst)
        self.assertEqual(inst['key'], WHATSAPP_INSTANCES[0]['key'])

    def test_get_instance_desconocida(self):
        self.assertIsNone(get_instance('no-existe'))

    def test_tres_instancias_configuradas(self):
        keys = {i['key'] for i in WHATSAPP_INSTANCES}
        self.assertEqual(keys, {'persona1', 'persona2', 'bot-4451076015'})

    def test_read_qr_state_json_null(self):
        """JSON null is valid JSON pero no es un dict — debe retornar no_data sin lanzar."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '.qr_state.json'
            p.write_text('null')
            data = read_qr_state(p)
            self.assertEqual(data['status'], 'no_data')
            self.assertIsNone(data['qr'])

    def test_read_qr_state_json_array(self):
        """JSON array es válido pero no es un dict — debe retornar no_data sin lanzar."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '.qr_state.json'
            p.write_text('[1, 2, 3]')
            data = read_qr_state(p)
            self.assertEqual(data['status'], 'no_data')
            self.assertIsNone(data['qr'])


class WhatsappQrListViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_wa_list', password='pass', is_staff=True
        )

    def test_redirige_anonimo(self):
        res = self.client.get('/panel/whatsapp/')
        self.assertEqual(res.status_code, 302)

    def test_accesible_staff(self):
        self.client.login(username='staff_wa_list', password='pass')
        res = self.client.get('/panel/whatsapp/')
        self.assertEqual(res.status_code, 200)

    def test_muestra_las_tres_instancias(self):
        self.client.login(username='staff_wa_list', password='pass')
        res = self.client.get('/panel/whatsapp/')
        self.assertContains(res, 'Persona 1')
        self.assertContains(res, 'Persona 2')
        self.assertContains(res, '4451076015')

    def test_nav_link_presente_en_dashboard(self):
        self.client.login(username='staff_wa_list', password='pass')
        res = self.client.get('/panel/')
        self.assertContains(res, 'WhatsApp QR')


class WhatsappQrDetailViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_wa_detail', password='pass', is_staff=True
        )
        self.client.login(username='staff_wa_detail', password='pass')

    def test_accesible_staff_key_valida(self):
        res = self.client.get('/panel/whatsapp/persona1/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Persona 1')

    def test_404_key_desconocida(self):
        res = self.client.get('/panel/whatsapp/no-existe/')
        self.assertEqual(res.status_code, 404)

    def test_redirige_anonimo(self):
        self.client.logout()
        res = self.client.get('/panel/whatsapp/persona1/')
        self.assertEqual(res.status_code, 302)


class WhatsappQrStatusEndpointTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_wa_status', password='pass', is_staff=True
        )
        self.client.login(username='staff_wa_status', password='pass')

    def test_404_key_desconocida(self):
        res = self.client.get('/panel/whatsapp/no-existe/status/')
        self.assertEqual(res.status_code, 404)

    def test_redirige_anonimo(self):
        self.client.logout()
        res = self.client.get('/panel/whatsapp/persona1/status/')
        self.assertEqual(res.status_code, 302)

    def test_sin_archivo_devuelve_no_data(self):
        # persona1 en el entorno de test no tiene .qr_state.json real
        res = self.client.get('/panel/whatsapp/persona1/status/')
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['status'], 'no_data')
        self.assertIsNone(body['qr'])

    def test_con_archivo_devuelve_su_contenido(self):
        from panel.whatsapp import WHATSAPP_INSTANCES
        import json as _json
        inst = next(i for i in WHATSAPP_INSTANCES if i['key'] == 'persona1')
        inst['state_path'].parent.mkdir(parents=True, exist_ok=True)
        inst['state_path'].write_text(_json.dumps({
            'status': 'qr', 'qr': 'test-qr-string', 'updated_at': '2026-08-03T10:00:00'
        }))
        try:
            res = self.client.get('/panel/whatsapp/persona1/status/')
            body = res.json()
            self.assertEqual(body['status'], 'qr')
            self.assertEqual(body['qr'], 'test-qr-string')
        finally:
            inst['state_path'].unlink(missing_ok=True)


class PedidosNuevosCountTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_notify_count', password='pass', is_staff=True
        )

    def _create_order(self, seen_at=None, status='pending'):
        return Order.objects.create(
            order_code=f'CNT-{uuid4().hex[:10]}', customer_name='Cliente',
            customer_phone='5550001111', status=status, seen_at=seen_at,
        )

    def test_requiere_staff(self):
        res = self.client.get(reverse('panel:pedidos_nuevos_count'))
        self.assertNotEqual(res.status_code, 200)

    def test_cuenta_solo_pendientes_no_vistos(self):
        self._create_order()                             # pending, no visto → cuenta
        self._create_order(seen_at=timezone.now())        # pending, ya visto → no cuenta
        self._create_order(status='confirmed')             # no pending → no cuenta
        self.client.login(username='staff_notify_count', password='pass')
        res = self.client.get(reverse('panel:pedidos_nuevos_count'))
        self.assertEqual(res.json(), {'count': 1})


class OrdersListMarkSeenTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_mark_seen', password='pass', is_staff=True
        )
        self.client.login(username='staff_mark_seen', password='pass')
        self.order = Order.objects.create(
            order_code=f'SEEN-{uuid4().hex[:10]}', customer_name='Cliente',
            customer_phone='5550004444', status='pending',
        )

    def test_visitar_orders_list_marca_seen_at(self):
        self.assertIsNone(self.order.seen_at)
        self.client.get(reverse('panel:orders_list'))
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.seen_at)

    def test_visitar_order_detail_marca_seen_at_de_otros_pendientes(self):
        # visto GLOBAL: entrar al detalle de UN pedido limpia el badge de TODOS
        otro = Order.objects.create(
            order_code=f'SEEN-OTHER-{uuid4().hex[:6]}', customer_name='Otro',
            customer_phone='5550005555', status='pending',
        )
        self.client.get(reverse('panel:order_detail', args=[self.order.pk]))
        otro.refresh_from_db()
        self.assertIsNotNone(otro.seen_at)
