from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from inventario.models import PrecioVenta
from inventario.tests.test_vistas_escaner import EscanerBase
from negocio.models import Pago, Pedido


class VenderBase(EscanerBase):
    def setUp(self):
        super().setUp()
        PrecioVenta.objects.create(tipo=self.base, nombre='Mayoreo', precio=Decimal('800'), orden=2)
        PrecioVenta.objects.create(tipo=self.base, nombre='Normal', precio=Decimal('900'), orden=1)
        self.p1, self.p2 = list(self.lote.pares.all())
        for p in (self.p1, self.p2):
            p.transicionar('en_stock', usuario=self.user, origen='escaner')

    def cerrar(self, lineas, metodo='efectivo'):
        return self.post_json(
            'inventario:vender_cerrar', {'lineas': lineas, 'metodo_pago': metodo})


class VenderInfoTests(VenderBase):
    def test_verde_con_precios_del_modelo_base_en_orden(self):
        r = self.post_json('inventario:vender_info', {'lectura': self.p1.codigo.lower()})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['nivel'], 'verde')
        self.assertEqual(d['par']['codigo'], self.p1.codigo)
        self.assertEqual(d['par']['talla'], '26')
        self.assertEqual(d['par']['costo'], '640.00')
        self.assertEqual([x['nombre'] for x in d['precios']], ['Normal', 'Mayoreo'])
        self.assertEqual(d['precios'][0]['precio'], '900.00')

    def test_rojo_si_no_esta_en_stock(self):
        self.p1.transicionar('vendido', usuario=self.user, origen='escaner')
        d = self.post_json('inventario:vender_info', {'lectura': self.p1.codigo}).json()
        self.assertEqual(d['nivel'], 'rojo')
        self.assertNotIn('precios', d)

    def test_rojo_si_no_existe(self):
        d = self.post_json('inventario:vender_info', {'lectura': 'NOEXISTE-1'}).json()
        self.assertEqual(d['nivel'], 'rojo')

    def test_json_invalido_400(self):
        r = self.client.post(reverse('inventario:vender_info'), 'x', content_type='application/json')
        self.assertEqual(r.status_code, 400)


class VenderCerrarTests(VenderBase):
    def test_vende_y_devuelve_pedido(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '900'},
                         {'codigo': self.p2.codigo, 'precio': 750.5}], 'transferencia')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d['ok'])
        pedido = Pedido.objects.get(pk=d['pedido_id'])
        self.assertEqual(pedido.precio_venta, Decimal('1650.50'))
        self.assertEqual(Pago.objects.get(pedido=pedido).metodo_pago, 'transferencia')
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'vendido')

    def test_precios_invalidos_400(self):
        for precio in ('', None, '-1', 'abc', 'NaN', 'Infinity', '-Infinity'):
            with self.subTest(precio=precio):
                r = self.cerrar([{'codigo': self.p1.codigo, 'precio': precio}])
                self.assertEqual(r.status_code, 400)
                self.assertFalse(r.json()['ok'])
                self.assertTrue(r.json()['error'])
        self.assertEqual(Pedido.objects.count(), 0)

    def test_metodo_invalido_400(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '900'}], 'bitcoin')
        self.assertEqual(r.status_code, 400)
        self.assertIn('pago', r.json()['error'].lower())

    def test_par_repetido_400(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '900'},
                         {'codigo': self.p1.codigo.lower(), 'precio': '900'}])
        self.assertEqual(r.status_code, 400)
        self.assertIn('repetido', r.json()['error'])
        self.assertEqual(Pedido.objects.count(), 0)

    def test_carrito_vacio_y_codigo_desconocido_400(self):
        self.assertEqual(self.cerrar([]).status_code, 400)
        r = self.cerrar([{'codigo': 'NOEXISTE-1', 'precio': '1'}])
        self.assertEqual(r.status_code, 400)

    def test_par_no_en_stock_400_sin_efectos(self):
        self.p2.transicionar('vendido', usuario=self.user, origen='escaner')
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '900'},
                         {'codigo': self.p2.codigo, 'precio': '900'}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'en_stock')

    def test_doble_envio_no_crea_segunda_venta(self):
        carrito = [{'codigo': self.p1.codigo, 'precio': '900'}]
        self.assertEqual(self.cerrar(carrito).status_code, 200)
        self.assertEqual(self.cerrar(carrito).status_code, 400)
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(Pago.objects.count(), 1)


class DeshacerVentaVistasTests(VenderBase):
    def setUp(self):
        super().setUp()
        pid = self.cerrar([{'codigo': self.p1.codigo, 'precio': '900'},
                           {'codigo': self.p2.codigo, 'precio': '800'}]).json()['pedido_id']
        self.pedido = Pedido.objects.get(pk=pid)

    def post(self, nombre, nota, **kw):
        return self.client.post(reverse(nombre, **kw), {'nota': nota})

    def test_cancelar_ok_redirige_al_pedido(self):
        r = self.post('inventario:venta_cancelar', 'error', kwargs={'pedido_pk': self.pedido.pk})
        self.assertRedirects(r, reverse('negocio:pedido_detail', args=[self.pedido.pk]),
                             fetch_redirect_response=False)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.CANCELADO)

    def test_cancelar_sin_nota_no_500_y_muestra_mensaje(self):
        r = self.client.post(
            reverse('inventario:venta_cancelar', kwargs={'pedido_pk': self.pedido.pk}),
            {'nota': ' '}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'La nota es obligatoria.')
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.PAGADO)

    def test_cancelar_ya_cancelado_no_500(self):
        url = reverse('inventario:venta_cancelar', kwargs={'pedido_pk': self.pedido.pk})
        self.client.post(url, {'nota': 'a'})
        r = self.client.post(url, {'nota': 'b'}, follow=True)
        self.assertContains(r, 'ya está cancelado')

    def test_quitar_par(self):
        r = self.post('inventario:venta_quitar_par', 'se cayo', kwargs={'codigo': self.p1.codigo})
        self.assertEqual(r.status_code, 302)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'en_stock')
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.precio_venta, Decimal('800'))

    def test_quitar_par_sin_venta_no_500(self):
        r = self.client.post(
            reverse('inventario:venta_quitar_par', kwargs={'codigo': 'NOEXISTE-1'}),
            {'nota': 'x'})
        self.assertEqual(r.status_code, 404)
        self.p1.transicionar('en_stock', usuario=self.user, origen='deshacer', nota='x')
        r = self.client.post(
            reverse('inventario:venta_quitar_par', kwargs={'codigo': self.p1.codigo}),
            {'nota': 'x'}, follow=True)
        self.assertContains(r, 'no está en una venta')

    def test_botones_en_detalle_solo_con_pares(self):
        r = self.client.get(reverse('negocio:pedido_detail', args=[self.pedido.pk]))
        self.assertContains(r, 'Cancelar venta')
        self.assertContains(r, reverse('inventario:venta_quitar_par', kwargs={'codigo': self.p1.codigo}))
        sin = Pedido.objects.create(costo_producto=Decimal('1'), precio_venta=Decimal('2'))
        r = self.client.get(reverse('negocio:pedido_detail', args=[sin.pk]))
        self.assertNotContains(r, 'Cancelar venta')


class StaffOnlyTests(VenderBase):
    def test_anonimo_y_no_staff_redirigidos(self):
        urls = [
            ('inventario:vender_info', {}), ('inventario:vender_cerrar', {}),
            ('inventario:venta_cancelar', {'pedido_pk': 1}),
            ('inventario:venta_quitar_par', {'codigo': 'X-1'})]
        for usuario in (None, get_user_model().objects.create_user('n', password='x')):
            self.client.logout()
            if usuario:
                self.client.force_login(usuario)
            for nombre, kw in urls:
                with self.subTest(nombre=nombre, usuario=usuario):
                    r = self.client.post(reverse(nombre, kwargs=kw), '{}',
                                         content_type='application/json')
                    self.assertEqual(r.status_code, 302)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_get_no_permitido(self):
        self.assertEqual(self.client.get(reverse('inventario:vender_cerrar')).status_code, 405)


class VenderMontosEnormesTests(VenderBase):
    def test_precio_enorme_es_400(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '1E+12'}])
        self.assertEqual(r.status_code, 400)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'en_stock')

    def test_limite_exacto_pasa_y_el_siguiente_no(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '99999999.99'}])
        self.assertEqual(r.status_code, 200, r.content)
        r = self.cerrar([{'codigo': self.p2.codigo, 'precio': '100000000'}])
        self.assertEqual(r.status_code, 400)
