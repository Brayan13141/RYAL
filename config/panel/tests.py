from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from catalog.models import Product, Category
from negocio.models import Cliente, Pedido
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
