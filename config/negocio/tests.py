import datetime
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
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


class GastoModelTest(TestCase):
    def test_str(self):
        g = Gasto(descripcion='Pago flete', monto=Decimal('150'), fecha=datetime.date.today())
        self.assertEqual(str(g), 'Pago flete — $150')

    def test_categoria_default_otro(self):
        g = Gasto.objects.create(
            fecha=datetime.date.today(),
            descripcion='Gasto misceláneo',
            monto=Decimal('50'),
        )
        self.assertEqual(g.categoria, Gasto.OTRO)

    def test_ordering_desc_por_fecha(self):
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        g1 = Gasto.objects.create(fecha=yesterday, descripcion='Viejo', monto=Decimal('100'))
        g2 = Gasto.objects.create(fecha=today, descripcion='Nuevo', monto=Decimal('200'))
        gastos = list(Gasto.objects.all())
        self.assertEqual(gastos[0].pk, g2.pk)  # más reciente primero


@override_settings(NEGOCIO_API_KEY='test-key-123')
class ApiClienteTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nombre='María', telefono='5551111111', descuento=Decimal('50')
        )

    def _get(self, telefono, key='test-key-123'):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {key}'} if key else {}
        return self.client.get(f'/api/negocio/cliente/{telefono}/', **headers)

    def test_cliente_con_descuento(self):
        res = self._get('5551111111')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['descuento'], 50.0)

    def test_cliente_sin_descuento_devuelve_cero(self):
        Cliente.objects.create(nombre='Sin desc', telefono='5552222222', descuento=Decimal('0'))
        res = self._get('5552222222')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['descuento'], 0.0)

    def test_cliente_no_registrado_devuelve_cero(self):
        res = self._get('9999999999')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['descuento'], 0.0)

    def test_sin_api_key_devuelve_401(self):
        res = self._get('5551111111', key=None)
        self.assertEqual(res.status_code, 401)

    def test_api_key_incorrecta_devuelve_401(self):
        res = self._get('5551111111', key='wrong-key')
        self.assertEqual(res.status_code, 401)

    def test_encuentra_cliente_por_jid_mexicano_521(self):
        # WhatsApp manda el JID como 521 + 10 dígitos; el cliente se guarda con 10
        res = self._get('5215551111111')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['descuento'], 50.0)

    def test_encuentra_cliente_por_jid_mexicano_52(self):
        res = self._get('525551111111')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['descuento'], 50.0)


class ClienteFormTest(TestCase):
    def test_normaliza_telefono_a_10_digitos(self):
        from negocio.forms import ClienteForm
        form = ClienteForm(data={
            'nombre': 'Pedro', 'telefono': '+52 1 (555) 222-3344',
            'descuento': '0', 'notas': '',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['telefono'], '5552223344')

    def test_rechaza_telefono_con_menos_de_10_digitos(self):
        from negocio.forms import ClienteForm
        form = ClienteForm(data={
            'nombre': 'Corto', 'telefono': '12345',
            'descuento': '0', 'notas': '',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('telefono', form.errors)


class ClientesViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='pass', is_staff=True
        )
        self.client.login(username='staff', password='pass')
        self.cliente = Cliente.objects.create(nombre='Test', telefono='5550000099')

    def test_clientes_list_accesible(self):
        res = self.client.get('/panel/negocio/clientes/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Test')

    def test_clientes_list_requiere_staff(self):
        self.client.logout()
        res = self.client.get('/panel/negocio/clientes/')
        self.assertEqual(res.status_code, 302)

    def test_cliente_create(self):
        res = self.client.post('/panel/negocio/clientes/nuevo/', {
            'nombre': 'Nuevo Cliente',
            'telefono': '5559999888',
            'descuento': '0',
            'notas': '',
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue(Cliente.objects.filter(telefono='5559999888').exists())

    def test_cliente_edit(self):
        res = self.client.post(f'/panel/negocio/clientes/{self.cliente.pk}/editar/', {
            'nombre': 'Editado',
            'telefono': '5550000099',
            'descuento': '50',
            'notas': '',
        })
        self.assertEqual(res.status_code, 302)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.nombre, 'Editado')
        self.assertEqual(self.cliente.descuento, Decimal('50'))

    def test_cliente_detail(self):
        res = self.client.get(f'/panel/negocio/clientes/{self.cliente.pk}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Test')


class PedidosViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff2', password='pass', is_staff=True
        )
        self.client.login(username='staff2', password='pass')
        self.cliente = Cliente.objects.create(nombre='Juan', telefono='5550001111')
        self.pedido = Pedido.objects.create(
            cliente=self.cliente,
            descripcion='Nike talla 42',
            costo_producto=Decimal('200'),
            precio_venta=Decimal('300'),
            envio=Decimal('50'),
        )

    def test_pedidos_list_accesible(self):
        res = self.client.get('/panel/negocio/pedidos/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Nike talla 42')

    def test_pedido_create(self):
        res = self.client.post('/panel/negocio/pedidos/nuevo/', {
            'cliente': self.cliente.pk,
            'descripcion': 'Jordan talla 43',
            'costo_producto': '250',
            'precio_venta': '350',
            'envio': '60',
            'estado': 'pendiente',
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue(Pedido.objects.filter(descripcion='Jordan talla 43').exists())

    def test_pedido_detail(self):
        res = self.client.get(f'/panel/negocio/pedidos/{self.pedido.pk}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Nike talla 42')

    def test_pedido_pago_add_json(self):
        import datetime
        from urllib.parse import urlencode
        res = self.client.post(
            f'/panel/negocio/pedidos/{self.pedido.pk}/pago/',
            urlencode({'fecha': datetime.date.today().isoformat(), 'monto': '200', 'notas': 'abono 1'}),
            content_type='application/x-www-form-urlencoded',
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['balance_pendiente'], '150.00')

    def test_pedido_pago_completo_cambia_estado(self):
        import datetime
        from urllib.parse import urlencode
        self.client.post(
            f'/panel/negocio/pedidos/{self.pedido.pk}/pago/',
            urlencode({'fecha': datetime.date.today().isoformat(), 'monto': '350', 'notas': ''}),
            content_type='application/x-www-form-urlencoded',
        )
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, Pedido.PAGADO)


class GastosViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff3', password='pass', is_staff=True
        )
        self.client.login(username='staff3', password='pass')

    def test_gastos_list_accesible(self):
        res = self.client.get('/panel/negocio/gastos/')
        self.assertEqual(res.status_code, 200)

    def test_gasto_create(self):
        import datetime
        res = self.client.post('/panel/negocio/gastos/', {
            'fecha': datetime.date.today().isoformat(),
            'descripcion': 'Pago proveedor lote gorras',
            'monto': '1500',
            'categoria': 'compra_proveedor',
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue(Gasto.objects.filter(descripcion='Pago proveedor lote gorras').exists())


class ResumenViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff4', password='pass', is_staff=True
        )
        self.client.login(username='staff4', password='pass')

    def test_resumen_accesible(self):
        res = self.client.get('/panel/negocio/')
        self.assertEqual(res.status_code, 200)

    def test_resumen_calcula_ganancia_neta(self):
        import datetime
        cliente = Cliente.objects.create(nombre='X', telefono='5550009999')
        p = Pedido.objects.create(
            cliente=cliente, descripcion='Test',
            costo_producto=Decimal('200'), precio_venta=Decimal('300'),
            envio=Decimal('0'), estado=Pedido.PAGADO,
        )
        Gasto.objects.create(
            fecha=datetime.date.today(), descripcion='Envío', monto=Decimal('50'), categoria='envio'
        )
        res = self.client.get('/panel/negocio/')
        self.assertContains(res, '100')  # ganancia bruta
        self.assertContains(res, '50')   # ganancia neta (100 - 50 gastos)
