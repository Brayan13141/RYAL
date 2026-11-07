"""Reporte de mayoreo contra menudeo (ventas del escáner)."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from negocio.models import Pedido, PedidoItem

URL = '/panel/negocio/mayoreo-menudeo/'


def pedido(fecha, tipo_venta, items, estado=Pedido.PAGADO, descuento='0'):
    """`items`: lista de (cantidad, precio_unitario, costo_unitario)."""
    p = Pedido.objects.create(
        fecha=fecha, estado=estado, origen=Pedido.TIENDA, tipo_venta=tipo_venta,
        precio_venta=sum(Decimal(pr) * c for c, pr, _ in items),
        costo_producto=sum(Decimal(co) * c for c, _, co in items),
        descuento_aplicado=Decimal(descuento))
    for c, pr, co in items:
        PedidoItem.objects.create(
            pedido=p, sku_snapshot='X', nombre_snapshot='Jordan 4', cantidad=c,
            precio_unitario=Decimal(pr), costo_unitario=Decimal(co))
    return p


class ReporteMayoreoMenudeoTests(TestCase):
    D1 = datetime.date(2026, 11, 3)
    D2 = datetime.date(2026, 11, 4)

    def setUp(self):
        User.objects.create_user(username='st', password='x', is_staff=True)
        self.client.login(username='st', password='x')

    def get(self, **params):
        return self.client.get(URL, {'desde': '2026-11-01', 'hasta': '2026-11-30', **params})

    def test_separa_mayoreo_menudeo_y_sin_clasificar(self):
        pedido(self.D1, 'mayoreo', [(12, '750', '490')])
        pedido(self.D1, 'menudeo', [(1, '1200', '640')])
        pedido(self.D2, 'menudeo', [(2, '1100', '640')], descuento='100')
        pedido(self.D2, None, [(1, '1000', '600')])  # venta vieja o del bot
        r = self.get()
        self.assertEqual(r.status_code, 200)
        t = r.context['totales']
        self.assertEqual(t['mayoreo'], {
            'pares': 12, 'ingreso': Decimal('9000'), 'ganancia': Decimal('3120')})
        self.assertEqual(t['menudeo'], {
            'pares': 3, 'ingreso': Decimal('3300'), 'ganancia': Decimal('1380')})
        self.assertEqual(t['sin_clasificar'], {
            'pares': 1, 'ingreso': Decimal('1000'), 'ganancia': Decimal('400')})

    def test_solo_cuenta_pagados_y_dentro_del_rango(self):
        pedido(self.D1, 'mayoreo', [(12, '750', '490')], estado=Pedido.CANCELADO)
        pedido(self.D1, 'mayoreo', [(12, '750', '490')], estado=Pedido.PENDIENTE)
        pedido(datetime.date(2026, 10, 31), 'mayoreo', [(12, '750', '490')])
        pedido(self.D1, 'mayoreo', [(1, '750', '490')])
        t = self.get().context['totales']
        self.assertEqual(t['mayoreo']['pares'], 1)

    def test_por_dia_y_por_mes(self):
        pedido(self.D1, 'mayoreo', [(12, '750', '490')])
        pedido(self.D2, 'menudeo', [(1, '1200', '640')])
        r = self.get()
        dias = {f['fecha']: f for f in r.context['por_dia']}
        self.assertEqual(set(dias), {self.D1, self.D2})
        self.assertEqual(dias[self.D1]['mayoreo']['pares'], 12)
        self.assertEqual(dias[self.D1]['menudeo']['pares'], 0)
        self.assertEqual(dias[self.D2]['menudeo']['ingreso'], Decimal('1200'))
        meses = r.context['por_mes']
        self.assertEqual(len(meses), 1)
        self.assertEqual(meses[0]['label'], 'Nov 2026')
        self.assertEqual(meses[0]['mayoreo']['ganancia'], Decimal('3120'))

    def test_sin_fechas_usa_el_mes_en_curso(self):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['desde'].day, 1)
        self.assertLessEqual(r.context['desde'], r.context['hasta'])

    def test_fecha_invalida_no_rompe(self):
        r = self.client.get(URL, {'desde': 'abc', 'hasta': '2026-99-99'})
        self.assertEqual(r.status_code, 200)

    def test_pantalla_muestra_las_dos_columnas(self):
        pedido(self.D1, 'mayoreo', [(12, '750', '490')])
        r = self.get()
        self.assertContains(r, 'Mayoreo')
        self.assertContains(r, 'Menudeo')

    def test_requiere_staff(self):
        self.client.logout()
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 302)
