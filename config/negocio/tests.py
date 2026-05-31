import datetime
from decimal import Decimal
from django.test import TestCase
from negocio.models import Cliente, Pedido, Pago, Gasto


class ClienteModelTest(TestCase):
    def test_str(self):
        c = Cliente(nombre='Ana López', telefono='5551234567')
        self.assertEqual(str(c), 'Ana López (5551234567)')

    def test_descuento_default_cero(self):
        c = Cliente.objects.create(nombre='Test', telefono='5550000001')
        self.assertEqual(c.descuento, Decimal('0'))


class PedidoModelTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Juan', telefono='5550000002')

    def _pedido(self, costo=200, precio=300, envio=50):
        return Pedido.objects.create(
            cliente=self.cliente,
            descripcion='Nike Air Max talla 42',
            costo_producto=Decimal(str(costo)),
            precio_venta=Decimal(str(precio)),
            envio=Decimal(str(envio)),
        )

    def test_total_a_cobrar_es_precio_mas_envio(self):
        p = self._pedido(precio=300, envio=50)
        self.assertEqual(p.total_a_cobrar, Decimal('350'))

    def test_ganancia_es_precio_menos_costo(self):
        p = self._pedido(costo=200, precio=300)
        self.assertEqual(p.ganancia, Decimal('100'))

    def test_balance_pendiente_sin_pagos(self):
        p = self._pedido(precio=300, envio=50)
        self.assertEqual(p.balance_pendiente, Decimal('350'))

    def test_balance_pendiente_con_abono_parcial(self):
        p = self._pedido(precio=300, envio=50)
        Pago.objects.create(pedido=p, fecha=datetime.date.today(), monto=Decimal('200'))
        self.assertEqual(p.balance_pendiente, Decimal('150'))

    def test_balance_pendiente_pagado_completo(self):
        p = self._pedido(precio=300, envio=50)
        Pago.objects.create(pedido=p, fecha=datetime.date.today(), monto=Decimal('350'))
        self.assertEqual(p.balance_pendiente, Decimal('0'))

    def test_estado_default_pendiente(self):
        p = self._pedido()
        self.assertEqual(p.estado, Pedido.PENDIENTE)
