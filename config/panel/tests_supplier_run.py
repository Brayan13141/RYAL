import importlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Product
from orders.models import Order, SupplierOrder


class SupplierRunTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='staff_run', password='pass', is_staff=True)
        self.client.login(username='staff_run', password='pass')
        self.order = Order.objects.create(
            order_code='RUN-1', customer_name='Ana', customer_phone='5512345678',
        )
        self.so = SupplierOrder.objects.create(order=self.order)
        self.url = reverse('panel:supplier_order_run', args=[self.order.pk])

    def _run(self):
        with patch('panel.views.subprocess.Popen') as popen:
            resp = self.client.post(self.url)
        return resp, popen

    def test_run_llena_run_started_at(self):
        resp, popen = self._run()
        self.assertTrue(resp.json()['ok'])
        popen.assert_called_once()
        self.so.refresh_from_db()
        self.assertIsNotNone(self.so.run_started_at)

    def test_reintento_conserva_run_started_at_original(self):
        self._run()
        self.so.refresh_from_db()
        original = self.so.run_started_at
        SupplierOrder.objects.filter(pk=self.so.pk).update(status='failed')
        self._run()
        self.so.refresh_from_db()
        self.assertEqual(self.so.run_started_at, original)

    def test_init_sigue_creando_items_igual(self):
        cat = Category.objects.create(name='Cat Run', slug='cat-run')
        con = Product.objects.create(
            name='Con', sku='RUN-CON', category=cat, base_price=Decimal('100'),
            supplier_url='https://www.modaverse.com/p/1',
        )
        sin = Product.objects.create(
            name='Sin', sku='RUN-SIN', category=cat, base_price=Decimal('100'),
        )
        order = Order.objects.create(order_code='RUN-2', customer_name='B', customer_phone='5500000000')
        for p in (con, sin):
            order.items.create(
                product=p, quantity=1, price_snapshot=Decimal('100'),
                sku_snapshot=p.sku, name_snapshot=p.name, variant_snapshot='M',
            )
        self.client.post(reverse('panel:supplier_order_init', args=[order.pk]))
        items = {i.order_item.sku_snapshot: i for i in order.supplier_order.items.all()}
        self.assertEqual(items['RUN-CON'].status, 'pending')
        self.assertEqual(items['RUN-CON'].supplier_url, 'https://www.modaverse.com/p/1')
        self.assertEqual(items['RUN-CON'].variant_target, 'M')
        self.assertEqual(items['RUN-SIN'].status, 'no_url')
        self.assertEqual(items['RUN-SIN'].supplier_url, '')

    def test_ya_lanzado(self):
        self.assertFalse(self.so.ya_lanzado)
        self.so.run_started_at = timezone.now()
        self.assertTrue(self.so.ya_lanzado)
        so2 = SupplierOrder.objects.create(
            order=Order.objects.create(order_code='RUN-3', customer_name='C', customer_phone='5500000001'),
        )
        oi = so2.order.items.create(
            product=None, quantity=1, price_snapshot=Decimal('1'), sku_snapshot='X', name_snapshot='X',
        )
        self.assertFalse(so2.ya_lanzado)
        so2.items.create(order_item=oi, status='added')
        self.assertTrue(so2.ya_lanzado)

    def test_migracion_marca_los_que_ya_corrieron(self):
        mig = importlib.import_module('orders.migrations.0015_orderitemchange_supplierorder_run_started_at')

        def nuevo(code, status, script):
            o = Order.objects.create(order_code=code, customer_name='M', customer_phone='5500000002')
            so = SupplierOrder.objects.create(order=o, status=status, cart_script=script)
            SupplierOrder.objects.filter(pk=so.pk).update(updated_at=timezone.now() - timedelta(days=3))
            return so.pk

        done = nuevo('MIG-1', 'done', '')
        con_script = nuevo('MIG-2', 'pending', 'x')
        virgen = nuevo('MIG-3', 'pending', '')
        mig.marcar_lanzados(apps, None)
        for pk in (done, con_script):
            so = SupplierOrder.objects.get(pk=pk)
            self.assertEqual(so.run_started_at, so.updated_at)
        self.assertIsNone(SupplierOrder.objects.get(pk=virgen).run_started_at)

    def test_item_update_marca_y_recalcula_estado(self):
        a = self.order.items.create(
            product=None, quantity=1, price_snapshot=Decimal('1'), sku_snapshot='A', name_snapshot='A')
        b = self.order.items.create(
            product=None, quantity=1, price_snapshot=Decimal('1'), sku_snapshot='B', name_snapshot='B')
        sa = self.so.items.create(order_item=a, status='pending')
        sb = self.so.items.create(order_item=b, status='pending')
        url = lambda si: reverse('panel:supplier_item_update', args=[self.order.pk, si.pk])

        data = self.client.post(url(sa), {'status': 'added'}).json()
        self.assertEqual((data['ok'], data['item_status'], data['order_status']), (True, 'added', 'partial'))
        self.assertEqual(data['item_notes'], 'Agregado manualmente')
        data = self.client.post(url(sb), {'status': 'added'}).json()
        self.assertEqual(data['order_status'], 'done')
        data = self.client.post(url(sa), {'status': 'pending'}).json()
        self.assertEqual((data['item_notes'], data['order_status']), ('', 'partial'))
        data = self.client.post(url(sa), {'status': 'otro'}).json()
        self.assertEqual(data, {'ok': False, 'error': 'Estado inválido'})
        sa.refresh_from_db()
        self.assertEqual(sa.status, 'pending')
        # Un ítem de otro pedido es 404.
        otro = Order.objects.create(order_code='RUN-9', customer_name='D', customer_phone='5500000009')
        resp = self.client.post(reverse('panel:supplier_item_update', args=[otro.pk, sa.pk]), {'status': 'added'})
        self.assertEqual(resp.status_code, 404)
