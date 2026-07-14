from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from catalog.models import Product, Category
from negocio.models import Cliente, Pedido, Pago
from orders.models import Order, OrderItem


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
    para checkout web (Order.deposit) y para WhatsApp/tienda (Pago parcial)."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_dash', password='pass', is_staff=True
        )
        self.client.login(username='staff_dash', password='pass')

        order = Order.objects.create(
            customer_name='Victor', customer_phone='5550001111',
            status='in_preparation', is_paid=False, deposit=Decimal('300'),
        )
        OrderItem.objects.create(
            order=order, product=None, quantity=1,
            price_snapshot=Decimal('800'), sku_snapshot='X', name_snapshot='X',
        )
        # total = 800, deposit = 300 -> balance_due = 500

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
