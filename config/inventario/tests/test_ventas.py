from decimal import Decimal

from django.test import TestCase

from inventario.models import Color, MovimientoPar, Par, TallaCalzado
from inventario.services.ventas import ligar_a_item, vender_pares
from inventario.tests.test_par import ParBase
from negocio.caja import caja_totales
from negocio.models import Cliente, Pago, Pedido, PedidoItem
from negocio.services import MOSTRADOR_TELEFONO

E = Par.Estado


class VenderParesTests(ParBase):
    def setUp(self):
        super().setUp()
        self.p1 = self.par(E.EN_STOCK)
        self.p2 = self.par(E.EN_STOCK)
        Par.objects.filter(pk=self.p1.pk).update(costo=Decimal('380'))
        Par.objects.filter(pk=self.p2.pk).update(costo=Decimal('400'))
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()

    def _vender(self):
        return vender_pares(
            [(self.p1, Decimal('900')), (self.p2, Decimal('750'))],
            metodo_pago='transferencia', usuario=self.user)

    def test_venta_de_dos_pares(self):
        pedido = self._vender()
        self.assertEqual(pedido.estado, Pedido.PAGADO)
        self.assertEqual(pedido.origen, Pedido.TIENDA)
        self.assertEqual(pedido.cliente.telefono, MOSTRADOR_TELEFONO)
        self.assertEqual(pedido.precio_venta, Decimal('1650'))
        self.assertEqual(pedido.costo_producto, Decimal('780'))
        items = {i.sku_snapshot: i for i in pedido.items.all()}
        self.assertEqual(items[self.p1.codigo].costo_unitario, Decimal('380'))
        self.assertEqual(items[self.p2.codigo].costo_unitario, Decimal('400'))
        self.assertEqual(items[self.p1.codigo].nombre_snapshot, 'Jordan 4 negro 27')
        pago = pedido.pagos.get()
        self.assertEqual(pago.monto, Decimal('1650'))
        self.assertEqual(pago.metodo_pago, 'transferencia')
        for p in (self.p1, self.p2):
            p.refresh_from_db()
            self.assertEqual(p.estado, E.VENDIDO)
            self.assertIsNotNone(p.pedido_item_id)
            self.assertEqual(p.movimientos.get().origen, 'escaner')

    def test_venta_escaneada_entra_a_caja(self):
        antes = caja_totales()['cobrado']
        self._vender()
        self.assertEqual(caja_totales()['cobrado'] - antes, Decimal('1650'))

    def test_par_no_en_stock_no_crea_nada(self):
        p3 = self.par(E.POR_RECIBIR)
        with self.assertRaises(ValueError):
            vender_pares([(self.p1, Decimal('900')), (p3, Decimal('700'))],
                         metodo_pago='efectivo', usuario=self.user)
        self.assertEqual(Pedido.objects.count(), 0)
        self.assertEqual(Pago.objects.count(), 0)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, E.EN_STOCK)

    def test_validaciones(self):
        casos = [
            ([], 'efectivo'),
            ([(self.p1, Decimal('1')), (self.p1, Decimal('1'))], 'efectivo'),
            ([(self.p1, Decimal('-1'))], 'efectivo'),
            ([(self.p1, Decimal('NaN'))], 'efectivo'),
            ([(self.p1, Decimal('Infinity'))], 'efectivo'),
            ([(self.p1, Decimal('10'))], 'bitcoin'),
        ]
        for lineas, metodo in casos:
            with self.subTest(lineas=lineas, metodo=metodo):
                with self.assertRaises(ValueError):
                    vender_pares(lineas, metodo_pago=metodo, usuario=self.user)
        self.assertEqual(Pedido.objects.count(), 0)
        self.assertEqual(Cliente.objects.filter(telefono=MOSTRADOR_TELEFONO).count(), 0)

    def test_reusa_cliente_mostrador(self):
        self._vender()
        p3 = self.par(E.EN_STOCK)
        vender_pares([(p3, Decimal('1'))], metodo_pago='efectivo', usuario=self.user)
        self.assertEqual(Cliente.objects.filter(telefono=MOSTRADOR_TELEFONO).count(), 1)


class LigarAItemTests(ParBase):
    def _item(self, cantidad=1):
        pedido = Pedido.objects.create(
            costo_producto=Decimal('50'), precio_venta=Decimal('900'),
            origen=Pedido.BOT, estado=Pedido.PENDIENTE)
        return PedidoItem.objects.create(
            pedido=pedido, sku_snapshot='TIENDA-BOT', nombre_snapshot='Jordan 4',
            cantidad=cantidad, costo_unitario=Decimal('50'), precio_unitario=Decimal('900'))

    def test_ligar_con_lugar_libre_no_crea_pedido_ni_pago(self):
        item = self._item()
        par = self.par(E.EN_STOCK)
        pedidos, pagos = Pedido.objects.count(), Pago.objects.count()
        r = ligar_a_item(par, item, usuario=self.user)
        self.assertEqual(r.nivel, 'verde')
        par.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(par.estado, E.VENDIDO)
        self.assertEqual(par.pedido_item_id, item.pk)
        self.assertEqual(item.costo_unitario, Decimal('50'))
        self.assertEqual(Pedido.objects.count(), pedidos)
        self.assertEqual(Pago.objects.count(), pagos)

    def test_intercambio_con_par_del_bot(self):
        item = self._item()
        del_bot = self.par(E.EN_STOCK)
        del_bot.transicionar('vendido', usuario=self.user, origen='bot', pedido_item=item)
        escaneado = self.par(E.EN_STOCK)
        r = ligar_a_item(escaneado, item, usuario=self.user)
        self.assertEqual(r.nivel, 'verde')
        del_bot.refresh_from_db()
        escaneado.refresh_from_db()
        self.assertEqual(del_bot.estado, E.EN_STOCK)
        self.assertIsNone(del_bot.pedido_item_id)
        self.assertEqual(escaneado.estado, E.VENDIDO)
        self.assertEqual(escaneado.pedido_item_id, item.pk)
        self.assertEqual(item.pares.count(), 1)
        mov = del_bot.movimientos.order_by('-pk').first()
        self.assertEqual(mov.origen, 'deshacer')
        self.assertEqual(mov.nota, f'Intercambio por escaneo de {escaneado.codigo}')

    def test_lleno_sin_par_del_bot_es_rojo(self):
        item = self._item()
        previo = self.par(E.EN_STOCK)
        previo.transicionar('vendido', usuario=self.user, origen='escaner', pedido_item=item)
        nuevo = self.par(E.EN_STOCK)
        r = ligar_a_item(nuevo, item, usuario=self.user)
        self.assertEqual(r.nivel, 'rojo')
        self.assertEqual(r.mensaje, 'Esa venta ya tiene todos sus pares')
        nuevo.refresh_from_db()
        self.assertEqual(nuevo.estado, E.EN_STOCK)

    def test_par_no_en_stock_es_rojo(self):
        item = self._item()
        par = self.par(E.POR_RECIBIR)
        self.assertEqual(ligar_a_item(par, item, usuario=self.user).nivel, 'rojo')

    def test_intercambio_exige_misma_variante(self):
        item = self._item()
        del_bot = self.par(E.EN_STOCK)
        del_bot.transicionar('vendido', usuario=self.user, origen='bot', pedido_item=item)
        otra_talla = TallaCalzado.objects.get_or_create(mx_cm=Decimal('28.0'))[0]
        escaneado = self.par(E.EN_STOCK)
        Par.objects.filter(pk=escaneado.pk).update(talla=otra_talla)
        escaneado.refresh_from_db()
        r = ligar_a_item(escaneado, item, usuario=self.user)
        self.assertEqual(r.nivel, 'rojo')
        del_bot.refresh_from_db()
        self.assertEqual(del_bot.estado, E.VENDIDO)
