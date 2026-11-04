import re
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from catalog.models import TipoArticulo
from inventario.codigos import generar_codigo
from inventario.models import (
    Color, Lote, MovimientoPar, Par, PrecioVenta, TallaCalzado, TransicionInvalida,
)
from negocio.models import Pedido, PedidoItem

E = Par.Estado


class ParBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('staff', password='x')
        self.tipo = TipoArticulo.objects.create(
            nombre='Jordan 4', keywords='jordan 4', costo=Decimal('100'))
        self.color = Color.objects.create(nombre='negro')
        self.talla = TallaCalzado.objects.get_or_create(mx_cm=Decimal('27.0'))[0]
        self.lote = Lote.objects.create(
            referencia='L1', origen='manual', via='aereo', flete_por_par=Decimal('280'))
        self._n = 0

    def par(self, estado=E.POR_RECIBIR, motivo_baja='', **kw):
        self._n += 1
        return Par.objects.create(
            codigo=f'JD4A-{self._n:04d}', tipo=self.tipo, color=self.color,
            talla=self.talla, lote=self.lote, estado=estado, motivo_baja=motivo_baja,
            costo=Decimal('1'), **kw)

    def pedido_item(self):
        pedido = Pedido.objects.create(costo_producto=Decimal('1'), precio_venta=Decimal('2'))
        return PedidoItem.objects.create(
            pedido=pedido, sku_snapshot='x', nombre_snapshot='x', cantidad=1,
            costo_unitario=Decimal('1'), precio_unitario=Decimal('2'))


class TransicionesTests(ParBase):
    def _casos(self):
        return [
            (E.POR_RECIBIR, '', E.EN_STOCK, dict(origen='escaner')),
            (E.POR_RECIBIR, '', E.BAJA, dict(origen='cierre_lote', motivo='faltante')),
            (E.BAJA, 'faltante', E.EN_STOCK, dict(origen='manual')),
            (E.EN_STOCK, '', E.VENDIDO, dict(origen='escaner')),
            (E.EN_STOCK, '', E.BAJA, dict(origen='manual', motivo='danado', nota='roto')),
            (E.VENDIDO, '', E.EN_STOCK, dict(origen='deshacer', nota='error')),
            (E.EN_STOCK, '', E.POR_RECIBIR, dict(origen='deshacer', nota='error')),
        ]

    def test_transiciones_permitidas(self):
        for de, motivo_baja, a, kw in self._casos():
            with self.subTest(de=de, a=a):
                p = self.par(de, motivo_baja)
                mov = p.transicionar(a, usuario=self.user, **kw)
                p.refresh_from_db()
                self.assertEqual(p.estado, a)
                self.assertEqual((mov.de_estado, mov.a_estado), (de, a))
                self.assertEqual(p.movimientos.count(), 1)

    def test_transiciones_prohibidas(self):
        casos = [
            (E.POR_RECIBIR, '', E.VENDIDO), (E.VENDIDO, '', E.BAJA),
            (E.BAJA, 'danado', E.EN_STOCK), (E.VENDIDO, '', E.POR_RECIBIR),
            (E.EN_STOCK, '', E.EN_STOCK),
        ]
        for de, motivo_baja, a in casos:
            with self.subTest(de=de, a=a):
                p = self.par(de, motivo_baja)
                with self.assertRaises(TransicionInvalida):
                    p.transicionar(a, usuario=self.user, origen='deshacer',
                                   nota='n', motivo='danado')
                p.refresh_from_db()
                self.assertEqual(p.estado, de)
                self.assertEqual(p.movimientos.count(), 0)

    def test_baja_faltante_vuelve_a_stock_pero_danado_no(self):
        p = self.par(E.BAJA, 'faltante')
        p.transicionar(E.EN_STOCK, usuario=self.user, origen='manual')
        p.refresh_from_db()
        self.assertEqual(p.motivo_baja, '')
        self.assertIsNotNone(p.recibido_at)
        q = self.par(E.BAJA, 'danado')
        with self.assertRaises(TransicionInvalida):
            q.transicionar(E.EN_STOCK, usuario=self.user, origen='manual')

    def test_baja_exige_motivo_valido_y_nota_si_danado_perdido(self):
        p = self.par(E.EN_STOCK)
        for kw in (dict(), dict(motivo='faltante', nota='x'), dict(motivo='danado'),
                   dict(motivo='perdido', nota=' '), dict(motivo='otro', nota='x')):
            with self.subTest(kw=kw), self.assertRaises(TransicionInvalida):
                p.transicionar(E.BAJA, usuario=self.user, origen='manual', **kw)
        p.refresh_from_db()
        self.assertEqual(p.estado, E.EN_STOCK)
        q = self.par(E.POR_RECIBIR)
        with self.assertRaises(TransicionInvalida):
            q.transicionar(E.BAJA, usuario=self.user, origen='cierre_lote')

    def test_origen_de_venta_restringido(self):
        p = self.par(E.EN_STOCK)
        with self.assertRaises(TransicionInvalida):
            p.transicionar(E.VENDIDO, usuario=self.user, origen='manual')

    def test_deshacer_exige_nota_y_origen(self):
        for de, a in ((E.VENDIDO, E.EN_STOCK), (E.EN_STOCK, E.POR_RECIBIR)):
            for kw in (dict(origen='manual', nota='x'), dict(origen='deshacer'),
                       dict(origen='deshacer', nota='  ')):
                with self.subTest(de=de, kw=kw):
                    p = self.par(de)
                    with self.assertRaises(TransicionInvalida):
                        p.transicionar(a, usuario=self.user, **kw)
                    p.refresh_from_db()
                    self.assertEqual(p.estado, de)

    def test_vender_setea_vendido_at_y_pedido_item(self):
        p = self.par(E.EN_STOCK)
        item = self.pedido_item()
        p.transicionar(E.VENDIDO, usuario=self.user, origen='bot', pedido_item=item)
        p.refresh_from_db()
        self.assertEqual(p.pedido_item, item)
        self.assertIsNotNone(p.vendido_at)

    def test_recibir_setea_recibido_at(self):
        p = self.par(E.POR_RECIBIR)
        p.transicionar(E.EN_STOCK, usuario=self.user, origen='escaner')
        p.refresh_from_db()
        self.assertIsNotNone(p.recibido_at)

    def test_salir_de_vendido_limpia_pedido_item(self):
        p = self.par(E.EN_STOCK)
        p.transicionar(E.VENDIDO, usuario=self.user, origen='escaner',
                       pedido_item=self.pedido_item())
        p.transicionar(E.EN_STOCK, usuario=self.user, origen='deshacer', nota='error')
        p.refresh_from_db()
        self.assertIsNone(p.pedido_item)
        self.assertIsNone(p.vendido_at)

    def test_relee_estado_del_registro_bloqueado(self):
        p = self.par(E.POR_RECIBIR)
        copia = Par.objects.get(pk=p.pk)
        p.transicionar(E.EN_STOCK, usuario=self.user, origen='escaner')
        with self.assertRaises(TransicionInvalida):
            copia.transicionar(E.EN_STOCK, usuario=self.user, origen='escaner')
        self.assertEqual(p.movimientos.count(), 1)


class CodigosTests(ParBase):
    def test_codigo_formato(self):
        self.assertTrue(re.fullmatch(
            r'JD4A-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}', generar_codigo('JD4A')))

    def test_codigo_reintenta_si_choca(self):
        self.par()
        Par.objects.update(codigo='JD4A-AAAA')
        with mock.patch('inventario.codigos.secrets.choice',
                        side_effect=list('AAAA') + list('BBBB')):
            self.assertEqual(generar_codigo('JD4A'), 'JD4A-BBBB'
)

    def test_codigo_agota_intentos(self):
        self.par()
        Par.objects.update(codigo='JD4A-AAAA')
        with mock.patch('inventario.codigos.secrets.choice', return_value='A'):
            with self.assertRaises(RuntimeError):
                generar_codigo('JD4A', intentos=3)


class PrecioVentaTests(ParBase):
    def test_precio_venta_solo_en_modelo_base(self):
        var = TipoArticulo(nombre='Jordan 4 A', keywords='jordan 4 aereo',
                           costo=Decimal('1'), padre=self.tipo, via='aereo', usa_tallas=True)
        var.full_clean()
        var.save()
        PrecioVenta(tipo=self.tipo, nombre='Normal', precio=Decimal('1500')).full_clean()
        with self.assertRaises(ValidationError):
            PrecioVenta(tipo=var, nombre='Normal', precio=Decimal('1500')).full_clean()
