from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from catalog.models import TipoArticulo
from inventario.models import Color, Lote, Par, TallaCalzado
from inventario.services.bot import faltantes_de_texto, parsear_venta_tenis
from negocio.models import Pedido
from negocio.services import VentaIncompleta, VentaSinTipo, crear_pedido_tienda_bot

E = Par.Estado


class VentaBotBase(TestCase):
    def setUp(self):
        self.base = TipoArticulo.objects.create(
            nombre='Jordan 4', keywords='jordan 4', costo=Decimal('100'))
        self.aereo = TipoArticulo.objects.create(
            nombre='Jordan 4 A', keywords='jordan 4 aereo, jordan 4 aéreo',
            costo=Decimal('650'), padre=self.base, via='aereo',
            usa_tallas=True, prefijo='JD4A')
        self.maritimo = TipoArticulo.objects.create(
            nombre='Jordan 4 M', keywords='jordan 4 maritimo, jordan 4 marítimo',
            costo=Decimal('550'), padre=self.base, via='maritimo',
            usa_tallas=True, prefijo='JD4M')
        self.negro = Color.objects.create(nombre='negro', alias='black')
        self.marino = Color.objects.create(nombre='marino')
        self.base.colores.add(self.negro, self.marino)
        self.t27 = TallaCalzado.objects.get_or_create(mx_cm=Decimal('27.0'))[0]
        self.t265 = TallaCalzado.objects.get_or_create(mx_cm=Decimal('26.5'))[0]
        self.lote = Lote.objects.create(
            referencia='L1', origen='manual', via='aereo', flete_por_par=Decimal('280'))
        self._n = 0

    def par(self, costo='500', color=None, talla=None, dias=0, estado=E.EN_STOCK):
        self._n += 1
        return Par.objects.create(
            codigo=f'JD4A-{self._n:04d}', tipo=self.aereo, color=color or self.negro,
            talla=talla or self.t27, lote=self.lote, estado=estado,
            costo=Decimal(costo),
            recibido_at=timezone.now() - timedelta(days=dias))

    def vender(self, texto, qty=1, **kw):
        return crear_pedido_tienda_bot(
            items=[{'description': texto, 'price': 1500, 'qty': qty}], **kw)


class FaltantesTests(VentaBotBase):
    def test_sin_via_no_crea_nada(self):
        with self.assertRaises(VentaIncompleta) as cm:
            self.vender('jordan 4 negro t27')
        d = cm.exception.detalles[0]
        self.assertEqual(d['falta'], 'via')
        self.assertIn('¿aéreo, marítimo o nacional?', d['mensaje'])
        self.assertIn('«jordan 4 aereo …»', d['mensaje'])
        self.assertEqual(Pedido.objects.count(), 0)

    def test_falta_color_nombra_los_del_modelo(self):
        with self.assertRaises(VentaIncompleta) as cm:
            self.vender('jordan 4 aereo t27')
        d = cm.exception.detalles[0]
        self.assertEqual(d['falta'], 'color')
        self.assertIn('negro', d['mensaje'])
        self.assertIn('marino', d['mensaje'])
        self.assertEqual(Pedido.objects.count(), 0)

    def test_falta_talla(self):
        with self.assertRaises(VentaIncompleta) as cm:
            self.vender('jordan 4 aereo negro')
        d = cm.exception.detalles[0]
        self.assertEqual(d['falta'], 'talla')
        self.assertEqual(d['mensaje'], 'Falta la talla: escríbela como t27 o t26.5')

    def test_sin_tipo_tiene_prioridad(self):
        with self.assertRaises(VentaSinTipo):
            crear_pedido_tienda_bot(items=[
                {'description': 'jordan 4 negro t27', 'price': 1, 'qty': 1},
                {'description': 'zzz raro', 'price': 1, 'qty': 1}])

    def test_talla_formatos(self):
        for txt, esperado in [('T27', self.t27), ('t26.5', self.t265),
                              ('t26,5', self.t265), ('t 27', self.t27)]:
            with self.subTest(txt=txt):
                color, talla, msgs = parsear_venta_tenis(
                    f'jordan 4 aereo negro {txt}', self.aereo)
                self.assertEqual(talla, esperado)
                self.assertEqual(color, self.negro)
                self.assertEqual(msgs, [])

    def test_talla_inexistente_o_inactiva_falta(self):
        TallaCalzado.objects.create(mx_cm=Decimal('29.5'), activa=False)
        for txt in ('t30', 't29.5'):
            with self.subTest(txt=txt):
                _, talla, msgs = parsear_venta_tenis(
                    f'jordan 4 aereo negro {txt}', self.aereo)
                self.assertIsNone(talla)
                self.assertTrue(msgs[0].startswith('Falta la talla'))
        det = faltantes_de_texto('jordan 4 aereo negro t30', self.aereo)
        self.assertEqual(det[0]['falta'], 'talla')

    def test_color_con_acento_y_mayusculas(self):
        cafe = Color.objects.create(nombre='cafe')
        self.base.colores.add(cafe)
        color, _, _ = parsear_venta_tenis('JORDAN 4 AEREO CAFÉ T27', self.aereo)
        self.assertEqual(color, cafe)

    def test_alias_de_color(self):
        color, _, _ = parsear_venta_tenis('jordan 4 aereo black t27', self.aereo)
        self.assertEqual(color, self.negro)

    def test_color_solo_palabra_completa(self):
        color, _, _ = parsear_venta_tenis('jordan 4 aereo negroide t27', self.aereo)
        self.assertIsNone(color)

    def test_gana_el_color_mas_largo(self):
        azul = Color.objects.create(nombre='azul')
        azul_marino = Color.objects.create(nombre='azul marino')
        self.base.colores.add(azul, azul_marino)
        color, _, _ = parsear_venta_tenis('jordan 4 aereo azul marino t27', self.aereo)
        self.assertEqual(color, azul_marino)

    def test_color_ambiguo(self):
        self.base.colores.add(Color.objects.create(nombre='verde'))
        _, _, msgs = parsear_venta_tenis('jordan 4 aereo negro verde t27', self.aereo)
        self.assertEqual(len(msgs), 1)
        self.assertIn('ambiguo', msgs[0])

    def test_jordan_4_marino_no_es_maritimo(self):
        pedido = self.vender('jordan 4 aereo marino t27')
        self.assertEqual(pedido.items.get().costo_unitario, Decimal('650.00'))
        self.assertEqual(pedido.sin_stock[0]['faltan'], 1)
        self.assertIn('Jordan 4 A marino 27', pedido.sin_stock[0]['mensaje'])

    def test_marino_no_cuenta_como_via(self):
        with self.assertRaises(VentaIncompleta) as cm:
            self.vender('jordan 4 marino t27')
        self.assertEqual(cm.exception.detalles[0]['falta'], 'via')
        self.assertEqual(Pedido.objects.count(), 0)

    def test_costo_de_la_linea_no_deriva_por_redondeo(self):
        self.par('500'); self.par('500'); self.par('501')
        pedido = self.vender('jordan 4 aereo negro t27', qty=3)
        self.assertEqual(pedido.items.get().costo_unitario, Decimal('500.33'))
        self.assertEqual(pedido.costo_producto, Decimal('1501.00'))


class AsignacionTests(VentaBotBase):
    def test_asigna_el_par_mas_antiguo(self):
        nuevo = self.par(costo='500', dias=1)
        viejo = self.par(costo='480', dias=10)
        pedido = self.vender('jordan 4 aereo negro t27')
        viejo.refresh_from_db()
        nuevo.refresh_from_db()
        self.assertEqual(viejo.estado, E.VENDIDO)
        self.assertEqual(nuevo.estado, E.EN_STOCK)
        item = pedido.items.get()
        self.assertEqual(viejo.pedido_item_id, item.pk)
        self.assertEqual(item.costo_unitario, Decimal('480.00'))
        self.assertEqual(pedido.costo_producto, Decimal('480.00'))
        self.assertEqual(pedido.sin_stock, [])
        self.assertEqual(viejo.movimientos.get().origen, 'bot')

    def test_dos_pares_con_uno_en_stock(self):
        p = self.par(costo='500')
        pedido = self.vender('jordan 4 aereo negro t27', qty=2)
        p.refresh_from_db()
        self.assertEqual(p.estado, E.VENDIDO)
        item = pedido.items.get()
        self.assertEqual(item.costo_unitario, Decimal('575.00'))  # (500+650)/2
        self.assertEqual(pedido.costo_producto, Decimal('1150.00'))
        self.assertEqual(pedido.sin_stock[0]['faltan'], 1)
        self.assertIn('Sin stock', pedido.sin_stock[0]['mensaje'])

    def test_no_toma_pares_de_otra_talla_ni_color(self):
        self.par(color=self.marino)
        self.par(talla=self.t265)
        pedido = self.vender('jordan 4 aereo negro t27')
        self.assertEqual(pedido.sin_stock[0]['faltan'], 1)
        self.assertEqual(Par.objects.filter(estado=E.VENDIDO).count(), 0)

    def test_tipo_sin_tallas_no_cambia(self):
        TipoArticulo.objects.create(nombre='Gorra', keywords='gorra', costo=Decimal('90'))
        pedido = self.vender('gorra negra', qty=2)
        self.assertEqual(pedido.items.get().costo_unitario, Decimal('90.00'))
        self.assertEqual(pedido.costo_producto, Decimal('180.00'))
        self.assertEqual(pedido.sin_stock, [])
        self.assertEqual(pedido.sin_tipo, [])
