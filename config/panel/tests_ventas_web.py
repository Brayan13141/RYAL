import datetime
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Product
from orders.models import Order, OrderItem
from panel.ventas_web import (
    historial_producto_web, lineas_venta_web, repartir_descuento,
    ventas_web_por_producto,
)


def _pedido(status='confirmed', descuento=0, created_at=None):
    o = Order.objects.create(
        customer_name='Cliente Tienda', customer_phone='5551112222',
        order_code=uuid4().hex[:12], status=status, is_paid=True, descuento_aplicado=Decimal(descuento),
    )
    if created_at is not None:
        Order.objects.filter(pk=o.pk).update(created_at=created_at)
        o.refresh_from_db()
    return o


def _item(order, product, qty=1, precio='300', costo='100', sku=None, nombre=None):
    return OrderItem.objects.create(
        order=order, product=product, quantity=qty,
        price_snapshot=Decimal(precio),
        cost_snapshot=None if costo is None else Decimal(costo),
        sku_snapshot=sku or (product.sku if product else 'X'),
        name_snapshot=nombre or (product.name if product else 'X'),
    )


class VentasWebTest(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Gorras', slug='gorras')
        self.gorra = self._producto('Gorra X', 'GX-1')

    def _producto(self, nombre, sku, base='100'):
        return Product.objects.create(
            name=nombre, sku=sku, category=self.cat,
            base_price=Decimal(base), is_active=True,
        )

    def test_repartir_descuento_cuadra_al_centavo(self):
        self.assertEqual(
            repartir_descuento(Decimal('100'), [Decimal('300')] * 3),
            [Decimal('33.33'), Decimal('33.33'), Decimal('33.34')])

    def test_repartir_descuento_cero_o_suma_cero(self):
        self.assertEqual(repartir_descuento(Decimal('0'), [Decimal('5')] * 2),
                         [Decimal('0'), Decimal('0')])
        self.assertEqual(repartir_descuento(Decimal('10'), [Decimal('0')] * 2),
                         [Decimal('0'), Decimal('0')])

    def test_agrupa_por_producto_y_excluye_cancelados(self):
        _item(_pedido(), self.gorra, qty=1)
        _item(_pedido(), self.gorra, qty=2)
        _item(_pedido(status='cancelled'), self.gorra, qty=5)
        filas = ventas_web_por_producto()
        self.assertEqual(len(filas), 1)
        fila = filas[0]
        self.assertEqual((fila['unidades'], fila['ingreso'], fila['ganancia']),
                         (3, Decimal('900'), Decimal('600')))
        self.assertFalse(fila['borrado'])

    def test_descuento_se_reparte(self):
        o = _pedido(descuento='100')
        for i in range(3):
            _item(o, self._producto(f'P{i}', f'P-{i}'))
        filas = ventas_web_por_producto()
        self.assertEqual(sum(f['ingreso'] for f in filas), Decimal('800'))

    def test_producto_borrado_se_agrupa_por_sku(self):
        _item(_pedido(), None, sku='OLD/1', nombre='Viejo')
        _item(_pedido(), None, sku='OLD/1', nombre='Viejo')
        filas = [f for f in ventas_web_por_producto() if f['borrado']]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['sku'], 'OLD/1')
        self.assertEqual(filas[0]['unidades'], 2)
        self.assertIsNone(filas[0]['product_pk'])
        self.assertEqual(filas[0]['nombre'], 'Viejo')

    def test_costo_usa_la_cadena_de_la_caja(self):
        p = self._producto('Gorra Y', 'GY-1', base='150')
        _item(_pedido(), p, costo=None)
        item = OrderItem.objects.get()
        fila = ventas_web_por_producto()[0]
        self.assertEqual(fila['costo'], item.costo_con_fuente()[0])
        self.assertEqual(
            fila['costo'], p.effective_base_price + p.effective_shipping)

    def test_fecha_local_mexico(self):
        utc = datetime.datetime(2026, 7, 16, 5, 30, tzinfo=datetime.timezone.utc)
        _item(_pedido(created_at=utc), self.gorra)
        self.assertEqual(ventas_web_por_producto()[0]['ultima_venta'],
                         datetime.date(2026, 7, 15))
        self.assertEqual(ventas_web_por_producto(desde=datetime.date(2026, 7, 16)), [])
        self.assertEqual(len(ventas_web_por_producto(hasta=datetime.date(2026, 7, 15))), 1)
        self.assertEqual(ventas_web_por_producto(hasta=datetime.date(2026, 7, 14)), [])

    def test_historial_suma_igual_que_la_fila(self):
        _item(_pedido(), self.gorra, qty=1)
        _item(_pedido(descuento='50'), self.gorra, qty=2)
        fila = ventas_web_por_producto()[0]
        hist = historial_producto_web(product_pk=fila['product_pk'])
        self.assertEqual(sum(l['ganancia'] for l in hist), fila['ganancia'])
        self.assertEqual(len(hist), 2)

    def test_historial_de_sku_borrado_y_orden_reciente_primero(self):
        v = timezone.make_aware(datetime.datetime(2026, 1, 1, 12))
        n = timezone.make_aware(datetime.datetime(2026, 3, 1, 12))
        _item(_pedido(created_at=v), None, sku='OLD/1')
        _item(_pedido(created_at=n), None, sku='OLD/1')
        hist = historial_producto_web(sku='OLD/1')
        self.assertEqual([h['fecha'] for h in hist],
                         [datetime.date(2026, 3, 1), datetime.date(2026, 1, 1)])

    def test_ordenes(self):
        otra = self._producto('Aaa', 'A-1')
        _item(_pedido(), self.gorra, qty=3)
        _item(_pedido(), otra, qty=1, precio='1000')
        self.assertEqual(ventas_web_por_producto(orden='unidades')[0]['sku'], 'GX-1')
        self.assertEqual(ventas_web_por_producto(orden='ingreso')[0]['sku'], 'A-1')
        self.assertEqual(ventas_web_por_producto(orden='nombre')[0]['sku'], 'A-1')
        self.assertEqual(ventas_web_por_producto(orden='basura')[0]['sku'], 'A-1')  # ganancia 900 vs 600

    def test_orden_ultima_venta(self):
        otra = self._producto('Bbb', 'B-1')
        _item(_pedido(created_at=timezone.make_aware(datetime.datetime(2026, 1, 1))), self.gorra)
        _item(_pedido(created_at=timezone.make_aware(datetime.datetime(2026, 3, 1))), otra)
        self.assertEqual([f['sku'] for f in ventas_web_por_producto(orden='ultima_venta')], ['B-1', 'GX-1'])


class VentasWebViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.objects.create_user(username='staff_vw', password='pass', is_staff=True)
        self.client.login(username='staff_vw', password='pass')
        cat = Category.objects.create(name='Gorras', slug='gorras')
        self.product = Product.objects.create(
            name='Gorra X', sku='GX-1', category=cat, base_price=Decimal('100'), is_active=True)
        self.order = _pedido()
        _item(self.order, self.product, qty=2)

    def test_staff_ve_la_tabla(self):
        res = self.client.get(reverse('panel:ventas_web'))
        self.assertContains(res, 'Gorra X')

    def test_anonimo_redirige(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('panel:ventas_web')).status_code, 302)

    def test_fecha_invalida_se_ignora(self):
        res = self.client.get(reverse('panel:ventas_web') + '?desde=abc&orden=xxx')
        self.assertEqual(res.status_code, 200)

    def test_sin_ventas_renderiza(self):
        Order.objects.all().delete()
        self.assertEqual(self.client.get(reverse('panel:ventas_web')).status_code, 200)

    def test_historial_de_borrado_con_slash(self):
        _item(_pedido(), None, sku='OLD/1', nombre='Viejo Borrado')
        res = self.client.get(reverse('panel:ventas_web'))
        self.assertContains(res, '?sku=OLD%2F1"')
        res = self.client.get(reverse('panel:ventas_web_borrado') + '?sku=OLD%2F1')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Viejo Borrado')

    def test_borrado_respeta_sku_con_espacios(self):
        _item(_pedido(), None, sku=' OLD ', nombre='Con Espacios')
        res = self.client.get(reverse('panel:ventas_web_borrado') + '?sku=%20OLD%20')
        self.assertContains(res, 'Con Espacios')

    def test_totales_van_antes_de_la_primera_fila(self):
        html = self.client.get(reverse('panel:ventas_web')).content.decode()
        self.assertLess(html.index('fila-totales'), html.index('Gorra X</a>'))
        html = self.client.get(reverse('panel:ventas_web_producto', args=[self.product.pk])).content.decode()
        self.assertLess(html.index('fila-totales'), html.index(self.order.order_code))

    def test_historial_muestra_precio_unitario(self):
        res = self.client.get(reverse('panel:ventas_web_producto', args=[self.product.pk]))
        self.assertContains(res, '$300.00')

    def test_borrado_sin_sku_es_404(self):
        self.assertEqual(self.client.get(reverse('panel:ventas_web_borrado')).status_code, 404)

    def test_producto_inexistente_es_404(self):
        self.assertEqual(self.client.get(reverse('panel:ventas_web_producto', args=[999999])).status_code, 404)

    def test_historial_linkea_al_pedido(self):
        res = self.client.get(reverse('panel:ventas_web_producto', args=[self.product.pk]))
        self.assertContains(res, f'href="{reverse("panel:order_detail", args=[self.order.pk])}"')

    def test_link_en_el_menu(self):
        self.assertContains(self.client.get(reverse('panel:dashboard')),
                            f'href="{reverse("panel:ventas_web")}"')
