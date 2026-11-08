"""Vender un par cuya llegada no se escaneó: se avisa y, si se confirma, se registra la
llegada y la venta en el mismo paso."""
from decimal import Decimal

from inventario.models import MovimientoPar, Par
from inventario.services import ventas
from inventario.services.lotes import cerrar_lote
from inventario.services.ventas import ligar_a_item
from inventario.tests.test_ligar_bot import E, LigarBotBase
from inventario.tests.test_vistas_vender import VenderBase
from negocio.models import Pedido


class SinRecibirBase(VenderBase):
    def setUp(self):
        super().setUp()
        # p1 en stock (escaneado); `pend` es un par cuya llegada nunca se escaneó.
        self.pend = Par.objects.create(
            codigo='PEND-0001', tipo=self.p1.tipo, color=self.p1.color, talla=self.p1.talla,
            lote=self.lote, costo=self.p1.costo, precio_proveedor=self.p1.precio_proveedor,
            flete=self.p1.flete)
        self.assertEqual(self.pend.estado, Par.Estado.POR_RECIBIR)

    def historial(self, par):
        return list(MovimientoPar.objects.filter(par=par).order_by('pk')
                    .values_list('de_estado', 'a_estado', 'origen'))


class VenderParesSinRecibirTests(SinRecibirBase):
    def vender(self, lineas, recibir=()):
        return ventas.vender_pares(lineas, metodo_pago='efectivo', usuario=self.staff,
                                   tipo_venta='menudeo', recibir=set(recibir))

    def test_sin_confirmar_no_se_vende(self):
        with self.assertRaisesMessage(ValueError, 'no se ha registrado su llegada'):
            self.vender([(self.pend, Decimal('900'))])
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, Par.Estado.POR_RECIBIR)
        self.assertFalse(Pedido.objects.exists())

    def test_confirmado_registra_llegada_y_venta(self):
        pedido = self.vender([(self.pend, Decimal('900'))], recibir={self.pend.pk})
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, Par.Estado.VENDIDO)
        self.assertIsNotNone(self.pend.recibido_at)
        self.assertEqual(self.pend.pedido_item.pedido, pedido)
        self.assertEqual(self.historial(self.pend)[-2:], [
            ('por_recibir', 'en_stock', 'escaner'), ('en_stock', 'vendido', 'escaner')])
        llegada = MovimientoPar.objects.filter(par=self.pend, a_estado='en_stock').get()
        self.assertIn('al venderlo', llegada.nota)

    def test_faltante_de_lote_cerrado_tambien(self):
        cerrar_lote(self.lote, usuario=self.staff)
        self.pend.refresh_from_db()
        self.assertEqual((self.pend.estado, self.pend.motivo_baja), ('baja', 'faltante'))
        with self.assertRaises(ValueError):
            self.vender([(self.pend, Decimal('900'))])
        self.vender([(self.pend, Decimal('900'))], recibir={self.pend.pk})
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, Par.Estado.VENDIDO)

    def test_mezclado_con_un_par_en_stock(self):
        pedido = self.vender([(self.p1, Decimal('900')), (self.pend, Decimal('800'))],
                             recibir={self.pend.pk})
        self.assertEqual(pedido.items.count(), 2)
        self.assertEqual(Par.objects.filter(estado='vendido').count(), 2)

    def test_todo_o_nada(self):
        self.p1.transicionar('vendido', usuario=self.staff, origen='escaner')
        with self.assertRaises(ValueError):
            self.vender([(self.pend, Decimal('900')), (self.p1, Decimal('900'))],
                        recibir={self.pend.pk})
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, Par.Estado.POR_RECIBIR)
        self.assertEqual(self.historial(self.pend), [])

    def test_anulado_nunca_se_vende(self):
        self.pend.transicionar('anulado', usuario=self.staff, origen='edicion')
        with self.assertRaisesMessage(ValueError, 'no está en stock'):
            self.vender([(self.pend, Decimal('900'))], recibir={self.pend.pk})

    def test_baja_por_danado_no_se_revive(self):
        self.p1.transicionar('baja', usuario=self.staff, origen='manual', nota='roto',
                             motivo='danado')
        with self.assertRaises(ValueError):
            self.vender([(self.p1, Decimal('900'))], recibir={self.p1.pk})


class VenderInfoSinRecibirTests(SinRecibirBase):
    def test_avisa_y_ofrece_registrar_la_llegada(self):
        d = self.post_json('inventario:vender_info', {'lectura': self.pend.codigo}).json()
        self.assertEqual(d['nivel'], 'amarillo')
        self.assertTrue(d['sin_recibir'])
        self.assertIn(f'lote #{self.lote.pk}', d['mensaje'])
        self.assertEqual(d['par']['codigo'], self.pend.codigo)
        self.assertTrue(d['precios'])

    def test_par_en_stock_no_trae_la_marca(self):
        d = self.post_json('inventario:vender_info', {'lectura': self.p1.codigo}).json()
        self.assertEqual(d['nivel'], 'verde')
        self.assertNotIn('sin_recibir', d)

    def test_anulado_sigue_en_rojo(self):
        self.pend.transicionar('anulado', usuario=self.staff, origen='edicion')
        d = self.post_json('inventario:vender_info', {'lectura': self.pend.codigo}).json()
        self.assertEqual(d['nivel'], 'rojo')


class VenderCerrarSinRecibirTests(SinRecibirBase):
    def test_sin_la_marca_400_con_mensaje(self):
        r = self.cerrar([{'codigo': self.pend.codigo, 'precio': '900'}])
        self.assertEqual(r.status_code, 400)
        self.assertIn('no se ha registrado su llegada', r.json()['error'])
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, Par.Estado.POR_RECIBIR)

    def test_con_la_marca_registra_llegada_y_venta(self):
        r = self.cerrar([{'codigo': self.pend.codigo, 'precio': '900', 'recibir': True}])
        self.assertEqual(r.status_code, 200, r.content)
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, Par.Estado.VENDIDO)

    def test_la_marca_debe_ser_booleana(self):
        r = self.cerrar([{'codigo': self.pend.codigo, 'precio': '900', 'recibir': 'si'}])
        self.assertEqual(r.status_code, 400)
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, Par.Estado.POR_RECIBIR)


class LigarBotSinRecibirTests(LigarBotBase):
    """El bot vendió uno sin stock; el par llegó pero nadie escaneó su llegada."""

    def setUp(self):
        super().setUp()
        self.pedido = self.vender('jordan 4 aereo negro t27', qty=1)  # sin stock: queda libre
        self.item = self.item_de(self.pedido)
        self.pend = self.par(estado=E.POR_RECIBIR)

    def ligar(self, **extra):
        return self.post_json('inventario:vender_ligar',
                              {'codigo': self.pend.codigo, 'item_id': self.item.pk, **extra})

    def test_sin_confirmar_es_rojo_y_no_cambia_nada(self):
        d = self.ligar().json()
        self.assertEqual(d['nivel'], 'rojo')
        self.assertIn('no se ha registrado su llegada', d['mensaje'])
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, E.POR_RECIBIR)

    def test_confirmado_registra_llegada_y_lo_liga(self):
        d = self.ligar(recibir=True).json()
        self.assertEqual(d['nivel'], 'verde', d)
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, E.VENDIDO)
        self.assertEqual(self.pend.pedido_item_id, self.item.pk)
        self.assertEqual(
            list(self.pend.movimientos.order_by('pk').values_list('de_estado', 'a_estado'))[-2:],
            [('por_recibir', 'en_stock'), ('en_stock', 'vendido')])

    def test_venta_llena_no_recibe_el_par(self):
        otro = self.par()
        ligar_a_item(otro, self.item, usuario=self.staff)  # llena el item por escaneo
        r = ligar_a_item(self.pend, self.item, usuario=self.staff, recibir=True)
        self.assertEqual(r.nivel, 'rojo')
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, E.POR_RECIBIR)

    def test_marca_no_booleana_400(self):
        r = self.ligar(recibir='si')
        self.assertEqual(r.status_code, 400)
