from decimal import Decimal

from django.utils import timezone

from inventario.models import Par
from inventario.services.ventas import (
    cancelar_pedido, quitar_par_de_pedido, vender_pares)
from inventario.tests.test_par import ParBase
from negocio.caja import caja_totales
from negocio.models import Pago, Pedido, PedidoItem
from negocio.services import ranking_por_tipo
from panel.views import _stats_pedido

E = Par.Estado


class DeshacerVentaTests(ParBase):
    def setUp(self):
        super().setUp()
        self.p1 = self.par(E.EN_STOCK)
        self.p2 = self.par(E.EN_STOCK)
        Par.objects.filter(pk=self.p1.pk).update(costo=Decimal('380'))
        Par.objects.filter(pk=self.p2.pk).update(costo=Decimal('400'))
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.caja_antes = caja_totales()['cobrado']

    def _vender(self):
        return vender_pares(
            [(self.p1, Decimal('900')), (self.p2, Decimal('750'))],
            metodo_pago='transferencia', usuario=self.user)

    def _pedido_bot(self):
        """Venta del bot: un ítem TIENDA-BOT de 2 pares, cobrada en efectivo."""
        pedido = Pedido.objects.create(
            costo_producto=Decimal('800'), precio_venta=Decimal('1800'),
            estado=Pedido.PAGADO, origen=Pedido.TIENDA)
        item = PedidoItem.objects.create(
            pedido=pedido, sku_snapshot='TIENDA-BOT', nombre_snapshot='jordan 4',
            cantidad=2, costo_unitario=Decimal('400'), precio_unitario=Decimal('900'))
        Pago.objects.create(pedido=pedido, fecha=timezone.localdate(),
                            monto=Decimal('1800'), metodo_pago='efectivo')
        return pedido, item

    def test_cancelar_devuelve_pares_y_caja(self):
        pedido = self._vender()
        cancelar_pedido(pedido, usuario=self.user, nota='cliente se arrepintio')
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)
        for p in (self.p1, self.p2):
            p.refresh_from_db()
            self.assertEqual(p.estado, E.EN_STOCK)
            self.assertIsNone(p.pedido_item_id)
            self.assertEqual(p.movimientos.order_by('-pk').first().origen, 'deshacer')
        reembolso = pedido.pagos.order_by('-pk').first()
        self.assertEqual(reembolso.monto, Decimal('-1650'))
        self.assertEqual(reembolso.metodo_pago, 'transferencia')
        self.assertEqual(reembolso.fecha, timezone.localdate())
        self.assertEqual(reembolso.notas, 'Reembolso: cliente se arrepintio')
        self.assertEqual(pedido.pagos.count(), 2)
        self.assertEqual(caja_totales()['cobrado'], self.caja_antes)

    def test_cancelado_no_cuenta_en_reportes(self):
        pedido = self._vender()
        cancelar_pedido(pedido, usuario=self.user, nota='x')
        rev, gan = _stats_pedido(Pedido.objects.filter(estado=Pedido.PAGADO))
        self.assertEqual((rev, gan), (0, 0))
        self.assertEqual(sum(f['piezas'] for f in ranking_por_tipo()), 0)

    def test_cancelar_dos_veces_no_reembolsa_dos_veces(self):
        pedido = self._vender()
        cancelar_pedido(pedido, usuario=self.user, nota='x')
        with self.assertRaisesMessage(ValueError, 'El pedido ya está cancelado'):
            cancelar_pedido(pedido, usuario=self.user, nota='x')
        self.assertEqual(pedido.pagos.count(), 2)
        self.assertEqual(caja_totales()['cobrado'], self.caja_antes)

    def test_nota_obligatoria(self):
        pedido = self._vender()
        for nota in ('', '   ', None):
            with self.assertRaises(ValueError):
                cancelar_pedido(pedido, usuario=self.user, nota=nota)
            with self.assertRaises(ValueError):
                quitar_par_de_pedido(self.p1, usuario=self.user, nota=nota)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.PAGADO)
        self.assertEqual(pedido.pagos.count(), 1)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, E.VENDIDO)
        self.assertIsNotNone(self.p1.pedido_item_id)

    def test_nota_larga_se_recorta_a_200(self):
        pedido = self._vender()
        cancelar_pedido(pedido, usuario=self.user, nota='x' * 300)
        self.assertEqual(len(pedido.pagos.order_by('-pk').first().notas), 200)

    def test_quitar_un_par_de_dos(self):
        pedido = self._vender()
        quitar_par_de_pedido(self.p2, usuario=self.user, nota='talla equivocada')
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.PAGADO)
        self.assertEqual(pedido.precio_venta, Decimal('900'))
        self.assertEqual(pedido.costo_producto, Decimal('380'))
        self.assertTrue(pedido.descripcion.startswith('1 art.: '))
        self.assertEqual(pedido.items.count(), 1)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.estado, E.EN_STOCK)
        self.assertIsNone(self.p2.pedido_item_id)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, E.VENDIDO)
        self.assertEqual(sum(p.monto for p in pedido.pagos.all()), Decimal('900'))
        self.assertEqual(caja_totales()['cobrado'] - self.caja_antes, Decimal('900'))
        rev, gan = _stats_pedido(Pedido.objects.filter(estado=Pedido.PAGADO))
        self.assertEqual((rev, gan), (900.0, 520.0))

    def test_quitar_el_unico_par_cancela(self):
        pedido = vender_pares([(self.p1, Decimal('900'))], metodo_pago='efectivo',
                              usuario=self.user)
        quitar_par_de_pedido(self.p1, usuario=self.user, nota='x')
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)
        self.assertEqual(sum(p.monto for p in pedido.pagos.all()), Decimal('0'))
        self.assertEqual(caja_totales()['cobrado'], self.caja_antes)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, E.EN_STOCK)

    def test_quitar_par_no_vendido_o_de_pedido_cancelado(self):
        pedido = self._vender()
        libre = self.par(E.EN_STOCK)
        with self.assertRaises(ValueError):
            quitar_par_de_pedido(libre, usuario=self.user, nota='x')
        cancelar_pedido(pedido, usuario=self.user, nota='x')
        with self.assertRaises(ValueError):
            quitar_par_de_pedido(self.p1, usuario=self.user, nota='x')

    def test_cancelar_venta_del_bot(self):
        pedido, item = self._pedido_bot()
        for p in (self.p1, self.p2):
            p.transicionar('vendido', usuario=self.user, origen='bot', pedido_item=item)
        cancelar_pedido(pedido, usuario=self.user, nota='bot erro')
        for p in (self.p1, self.p2):
            p.refresh_from_db()
            self.assertEqual(p.estado, E.EN_STOCK)
        self.assertEqual(caja_totales()['cobrado'], self.caja_antes)

    def test_quitar_par_de_item_del_bot_con_cantidad_2(self):
        pedido, item = self._pedido_bot()
        self.p1.transicionar('vendido', usuario=self.user, origen='bot', pedido_item=item)
        quitar_par_de_pedido(self.p1, usuario=self.user, nota='x')
        item.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(item.cantidad, 1)
        self.assertEqual(pedido.precio_venta, Decimal('900'))
        self.assertEqual(pedido.costo_producto, Decimal('400'))
        self.assertEqual(sum(p.monto for p in pedido.pagos.all()), Decimal('900'))

    def test_quitar_par_de_pedido_no_pagado_no_cambia_nada(self):
        pedido = self._vender()
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.PENDIENTE)
        with self.assertRaises(ValueError):
            quitar_par_de_pedido(self.p1, usuario=self.user, nota='x')
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.PENDIENTE)
        self.assertEqual(pedido.precio_venta, Decimal('1650'))
        self.assertEqual(pedido.items.count(), 2)
        self.assertEqual(pedido.pagos.count(), 1)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, E.VENDIDO)
        self.assertEqual(caja_totales()['cobrado'] - self.caja_antes, Decimal('1650'))

    def test_quitar_los_dos_pares_de_un_item_cantidad_2_cancela(self):
        pedido, item = self._pedido_bot()
        for p in (self.p1, self.p2):
            p.transicionar('vendido', usuario=self.user, origen='bot', pedido_item=item)
        quitar_par_de_pedido(self.p1, usuario=self.user, nota='x')
        quitar_par_de_pedido(self.p2, usuario=self.user, nota='x')
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.CANCELADO)
        self.assertFalse(pedido.items.exists())
        self.assertEqual(sum(p.monto for p in pedido.pagos.all()), Decimal('0'))
