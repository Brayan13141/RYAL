"""Cambio de par: el cliente regresa un par vendido y se lleva otro en la misma venta."""
from decimal import Decimal

from django.utils import timezone

from django.urls import reverse

from inventario.models import Par, TallaCalzado
from inventario.services.cambios import cambiar_par
from inventario.services.ventas import vender_pares
from inventario.tests.test_par import ParBase
from negocio.models import Pago, Pedido, PedidoItem

E = Par.Estado


class CambioParBase(ParBase):
    def setUp(self):
        super().setUp()
        self.vendido = self.par(E.EN_STOCK)
        Par.objects.filter(pk=self.vendido.pk).update(costo=Decimal('380'))
        self.vendido.refresh_from_db()
        self.otro_vendido = self.par(E.EN_STOCK)
        self.pedido = vender_pares(
            [(self.vendido, Decimal('900')), (self.otro_vendido, Decimal('800'))],
            metodo_pago='transferencia', usuario=self.user, tipo_venta='menudeo')
        self.vendido.refresh_from_db()
        # El que se lleva: otra talla y otro costo.
        self.talla_26 = TallaCalzado.objects.get_or_create(mx_cm=Decimal('26.0'))[0]
        self.nuevo = self.par(E.EN_STOCK)
        Par.objects.filter(pk=self.nuevo.pk).update(costo=Decimal('420'), talla=self.talla_26)
        self.nuevo.refresh_from_db()

    def cambiar(self, precio='900', **kw):
        kw.setdefault('metodo_pago', None)
        return cambiar_par(self.vendido, self.nuevo, precio=Decimal(precio),
                           usuario=self.user, **kw)

    def cobrado(self):
        return sum((p.monto for p in self.pedido.pagos.all()), Decimal('0'))


class CambioMismoPrecioTests(CambioParBase):
    def test_regresado_a_stock_y_nuevo_vendido_en_la_misma_venta(self):
        res = self.cambiar('900')
        self.vendido.refresh_from_db()
        self.nuevo.refresh_from_db()
        self.assertEqual(res.pedido.pk, self.pedido.pk)
        self.assertEqual(self.vendido.estado, E.EN_STOCK)
        self.assertIsNone(self.vendido.pedido_item_id)
        self.assertEqual(self.nuevo.estado, E.VENDIDO)
        self.assertEqual(self.nuevo.pedido_item.pedido_id, self.pedido.pk)

    def test_renglon_pasa_al_par_nuevo_con_su_costo(self):
        self.cambiar('900')
        self.nuevo.refresh_from_db()
        item = self.nuevo.pedido_item
        self.assertEqual(item.sku_snapshot, self.nuevo.codigo)
        self.assertIn('26', item.nombre_snapshot)
        self.assertEqual(item.costo_unitario, Decimal('420'))
        self.assertEqual(item.precio_unitario, Decimal('900'))
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.costo_producto, Decimal('421'))  # 420 + 1 del otro par
        self.assertEqual(self.pedido.precio_venta, Decimal('1700'))
        self.assertEqual(self.pedido.items.count(), 2)

    def test_sin_diferencia_no_toca_la_caja(self):
        antes = self.cobrado()
        res = self.cambiar('900')
        self.assertEqual(res.diferencia, Decimal('0'))
        self.assertEqual(self.cobrado(), antes)

    def test_historial_de_los_dos_pares_apunta_al_otro(self):
        self.cambiar('900', nota='le quedaba chico')
        mov_r = self.vendido.movimientos.order_by('-pk').first()
        mov_n = self.nuevo.movimientos.order_by('-pk').first()
        self.assertEqual((mov_r.de_estado, mov_r.a_estado), ('vendido', 'en_stock'))
        self.assertIn(f'Cambio: regresó por {self.nuevo.codigo}', mov_r.nota)
        self.assertIn('le quedaba chico', mov_r.nota)
        self.assertEqual((mov_n.de_estado, mov_n.a_estado), ('en_stock', 'vendido'))
        self.assertIn(f'Cambio: se llevó en lugar de {self.vendido.codigo}', mov_n.nota)


class CambioDiferenciaTests(CambioParBase):
    def test_mas_caro_cobra_la_diferencia_en_la_venta(self):
        antes = self.cobrado()
        res = self.cambiar('1200', metodo_pago='efectivo')
        self.assertEqual(res.diferencia, Decimal('300'))
        self.assertEqual(self.cobrado(), antes + Decimal('300'))
        pago = self.pedido.pagos.order_by('-pk').first()
        self.assertEqual((pago.monto, pago.metodo_pago), (Decimal('300'), 'efectivo'))
        self.assertIn('Cambio', pago.notas)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.precio_venta, Decimal('2000'))

    def test_mas_caro_sin_metodo_de_pago_no_hace_nada(self):
        with self.assertRaisesMessage(ValueError, 'método de pago'):
            self.cambiar('1200')
        self.vendido.refresh_from_db()
        self.nuevo.refresh_from_db()
        self.assertEqual((self.vendido.estado, self.nuevo.estado), (E.VENDIDO, E.EN_STOCK))

    def test_mas_barato_no_devuelve_dinero_y_conserva_el_ingreso(self):
        antes = self.cobrado()
        res = self.cambiar('750', metodo_pago='efectivo')
        self.assertEqual(res.diferencia, Decimal('0'))
        self.assertEqual(self.cobrado(), antes)
        self.nuevo.refresh_from_db()
        self.assertEqual(self.nuevo.pedido_item.precio_unitario, Decimal('900'))
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.precio_venta, Decimal('1700'))

    def test_precio_invalido(self):
        for malo in ('-1', 'NaN'):
            with self.subTest(malo=malo), self.assertRaises(ValueError):
                self.cambiar(malo)


class CambioDanadoTests(CambioParBase):
    def test_regresa_danado_queda_de_baja(self):
        self.cambiar('900', danado=True)
        self.vendido.refresh_from_db()
        self.assertEqual((self.vendido.estado, self.vendido.motivo_baja), (E.BAJA, 'danado'))
        ultimo = self.vendido.movimientos.order_by('-pk').first()
        self.assertEqual((ultimo.de_estado, ultimo.a_estado), ('en_stock', 'baja'))
        self.assertIn('dañado', ultimo.nota)


class CambioValidacionesTests(CambioParBase):
    def assert_nada_cambio(self):
        self.vendido.refresh_from_db()
        self.nuevo.refresh_from_db()
        self.assertEqual((self.vendido.estado, self.nuevo.estado), (E.VENDIDO, E.EN_STOCK))

    def test_el_regresado_tiene_que_estar_vendido(self):
        suelto = self.par(E.EN_STOCK)
        with self.assertRaisesMessage(ValueError, 'no está vendido'):
            cambiar_par(suelto, self.nuevo, precio=Decimal('900'), metodo_pago=None,
                        usuario=self.user)

    def test_el_nuevo_tiene_que_estar_en_stock(self):
        Par.objects.filter(pk=self.nuevo.pk).update(estado=E.POR_RECIBIR)
        with self.assertRaisesMessage(ValueError, 'no está en stock'):
            self.cambiar('900')
        self.vendido.refresh_from_db()
        self.assertEqual(self.vendido.estado, E.VENDIDO)

    def test_no_se_cambia_por_si_mismo(self):
        with self.assertRaises(ValueError):
            cambiar_par(self.vendido, self.vendido, precio=Decimal('900'), metodo_pago=None,
                        usuario=self.user)

    def test_venta_cancelada_no_admite_cambio(self):
        Pedido.objects.filter(pk=self.pedido.pk).update(estado=Pedido.CANCELADO)
        with self.assertRaisesMessage(ValueError, 'cancelada'):
            self.cambiar('900')
        self.assert_nada_cambio()


class CambioVentaBotTests(CambioParBase):
    """Un ítem del bot con 2 pares iguales: se parte y solo cambia uno."""
    def test_item_de_varios_pares_se_parte(self):
        pedido = Pedido.objects.create(
            costo_producto=Decimal('760'), precio_venta=Decimal('1800'),
            estado=Pedido.PAGADO, origen=Pedido.TIENDA)
        item = PedidoItem.objects.create(
            pedido=pedido, sku_snapshot='TIENDA-BOT', nombre_snapshot='jordan 4 negro 27',
            cantidad=2, costo_unitario=Decimal('380'), precio_unitario=Decimal('900'))
        Pago.objects.create(pedido=pedido, fecha=timezone.localdate(),
                            monto=Decimal('1800'), metodo_pago='efectivo')
        a, b = self.par(E.EN_STOCK), self.par(E.EN_STOCK)
        for p in (a, b):
            p.transicionar('vendido', usuario=self.user, origen='bot', pedido_item=item)

        cambiar_par(a, self.nuevo, precio=Decimal('900'), metodo_pago=None, usuario=self.user)

        item.refresh_from_db()
        b.refresh_from_db()
        self.nuevo.refresh_from_db()
        self.assertEqual(item.cantidad, 1)
        self.assertEqual(b.pedido_item_id, item.pk)
        nuevo_item = self.nuevo.pedido_item
        self.assertNotEqual(nuevo_item.pk, item.pk)
        self.assertEqual((nuevo_item.pedido_id, nuevo_item.cantidad), (pedido.pk, 1))
        self.assertEqual(nuevo_item.costo_unitario, Decimal('420'))
        pedido.refresh_from_db()
        self.assertEqual(pedido.precio_venta, Decimal('1800'))
        self.assertEqual(pedido.costo_producto, Decimal('800'))  # 380 + 420


# ── Vistas del modo Cambio ───────────────────────────────────────────────────

from inventario.models import PrecioLista  # noqa: E402
from inventario.tests.test_vistas_vender import VenderBase  # noqa: E402


class CambioVistasTests(VenderBase):
    def setUp(self):
        super().setUp()
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '900'}], tipo_venta='mayoreo')
        self.pedido_id = r.json()['pedido_id']

    def test_regresa_verde_con_la_venta(self):
        d = self.post_json('inventario:cambio_regresa', {'lectura': self.p1.codigo.lower()}).json()
        self.assertEqual(d['nivel'], 'verde')
        self.assertEqual(d['par']['codigo'], self.p1.codigo)
        self.assertEqual(d['venta']['pedido_id'], self.pedido_id)
        self.assertEqual(d['venta']['precio_pagado'], '900.00')
        self.assertNotIn('costo', d['par'])

    def test_regresa_rojo_si_no_esta_vendido(self):
        d = self.post_json('inventario:cambio_regresa', {'lectura': self.p2.codigo}).json()
        self.assertEqual(d['nivel'], 'rojo')
        d = self.post_json('inventario:cambio_regresa', {'lectura': 'NOEXISTE-1'}).json()
        self.assertEqual(d['nivel'], 'rojo')

    def test_nuevo_sugiere_precio_de_lista_del_tipo_de_venta(self):
        PrecioLista.objects.update_or_create(
            calidad='importado', tipo_venta='mayoreo', defaults={'precio': Decimal('760')})
        d = self.post_json('inventario:cambio_nuevo',
                           {'lectura': self.p2.codigo, 'regresa': self.p1.codigo}).json()
        self.assertEqual(d['nivel'], 'verde')
        self.assertEqual(d['par']['codigo'], self.p2.codigo)
        self.assertEqual(d['precio_sugerido'], '760.00')

    def test_nuevo_sin_lista_sugiere_lo_pagado(self):
        PrecioLista.objects.all().delete()
        d = self.post_json('inventario:cambio_nuevo',
                           {'lectura': self.p2.codigo, 'regresa': self.p1.codigo}).json()
        self.assertEqual(d['precio_sugerido'], '900.00')

    def test_nuevo_rojo_si_no_esta_en_stock(self):
        d = self.post_json('inventario:cambio_nuevo',
                           {'lectura': self.p1.codigo, 'regresa': self.p1.codigo}).json()
        self.assertEqual(d['nivel'], 'rojo')

    def test_confirmar_cobra_diferencia(self):
        r = self.post_json('inventario:cambio_confirmar', {
            'regresa': self.p1.codigo, 'nuevo': self.p2.codigo, 'precio': '1000',
            'metodo_pago': 'efectivo', 'danado': False, 'nota': ''})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d['ok'])
        self.assertEqual((d['pedido_id'], d['diferencia']), (self.pedido_id, '100.00'))
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual((self.p1.estado, self.p2.estado), ('en_stock', 'vendido'))

    def test_confirmar_danado(self):
        self.post_json('inventario:cambio_confirmar', {
            'regresa': self.p1.codigo, 'nuevo': self.p2.codigo, 'precio': '900',
            'danado': True})
        self.p1.refresh_from_db()
        self.assertEqual((self.p1.estado, self.p1.motivo_baja), ('baja', 'danado'))

    def test_confirmar_errores_400(self):
        casos = [
            {'regresa': self.p1.codigo, 'nuevo': self.p2.codigo, 'precio': 'x'},
            {'regresa': self.p1.codigo, 'nuevo': 'NOEXISTE-1', 'precio': '900'},
            {'regresa': self.p1.codigo, 'nuevo': self.p2.codigo, 'precio': '1000'},  # sin método
        ]
        for datos in casos:
            with self.subTest(datos=datos):
                r = self.post_json('inventario:cambio_confirmar', datos)
                self.assertEqual(r.status_code, 400)
                self.assertFalse(r.json()['ok'])
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'vendido')

    def test_pantalla_trae_el_modo_cambio(self):
        r = self.client.get(reverse('inventario:escanear'))
        self.assertContains(r, 'tab-cambio')
        self.assertContains(r, reverse('inventario:cambio_confirmar'))

    def test_solo_staff(self):
        self.client.logout()
        r = self.post_json('inventario:cambio_regresa', {'lectura': self.p1.codigo})
        self.assertEqual(r.status_code, 302)
