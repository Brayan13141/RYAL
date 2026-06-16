import datetime
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from negocio.models import Cliente, Pedido, Pago, Gasto, PedidoItem
from catalog.models import Category, Product
from negocio.services import crear_venta_tienda, VentaInvalida


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


class PagoMetodoTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Pos', telefono='5559990000')
        self.pedido = Pedido.objects.create(
            cliente=self.cliente, descripcion='x',
            costo_producto=Decimal('100'), precio_venta=Decimal('200'),
        )

    def test_metodo_pago_default_efectivo(self):
        pago = Pago.objects.create(
            pedido=self.pedido, fecha=datetime.date.today(), monto=Decimal('200'),
        )
        self.assertEqual(pago.metodo_pago, 'efectivo')

    def test_metodo_pago_transferencia(self):
        pago = Pago.objects.create(
            pedido=self.pedido, fecha=datetime.date.today(),
            monto=Decimal('200'), metodo_pago='transferencia',
        )
        self.assertEqual(pago.metodo_pago, 'transferencia')


class PedidoTiendaTest(TestCase):
    def test_pedido_sin_cliente_es_valido(self):
        # Venta de mostrador anónima: cliente nullable
        p = Pedido.objects.create(
            descripcion='Venta mostrador',
            costo_producto=Decimal('100'), precio_venta=Decimal('200'),
        )
        self.assertIsNone(p.cliente)

    def test_origen_default_whatsapp(self):
        p = Pedido.objects.create(
            descripcion='x', costo_producto=Decimal('100'), precio_venta=Decimal('200'),
        )
        self.assertEqual(p.origen, 'whatsapp')

    def test_origen_tienda(self):
        p = Pedido.objects.create(
            descripcion='x', costo_producto=Decimal('100'),
            precio_venta=Decimal('200'), origen='tienda',
        )
        self.assertEqual(p.origen, 'tienda')

    def test_str_con_cliente(self):
        cliente = Cliente.objects.create(nombre='Juan', telefono='5550000099')
        p = Pedido.objects.create(
            cliente=cliente, descripcion='x',
            costo_producto=Decimal('100'), precio_venta=Decimal('200'),
        )
        self.assertEqual(str(p), f'Pedido #{p.pk} — Juan')

    def test_str_sin_cliente_muestra_mostrador(self):
        p = Pedido.objects.create(
            descripcion='x', costo_producto=Decimal('100'), precio_venta=Decimal('200'),
        )
        self.assertEqual(str(p), f'Pedido #{p.pk} — Mostrador')


class PedidoItemTest(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Gorras', profit_margin=Decimal('100'))
        self.product = Product.objects.create(
            sku='CAP-001', name='Gorra NY', category=self.cat, base_price=Decimal('150'),
        )
        self.pedido = Pedido.objects.create(
            descripcion='x', costo_producto=Decimal('0'), precio_venta=Decimal('0'),
        )

    def test_subtotal_y_costo_total(self):
        item = PedidoItem.objects.create(
            pedido=self.pedido, product=self.product,
            sku_snapshot='CAP-001', nombre_snapshot='Gorra NY',
            cantidad=3, costo_unitario=Decimal('150'), precio_unitario=Decimal('250'),
        )
        self.assertEqual(item.subtotal, Decimal('750'))      # 250 * 3
        self.assertEqual(item.costo_total, Decimal('450'))   # 150 * 3

    def test_item_sobrevive_si_se_borra_el_producto(self):
        item = PedidoItem.objects.create(
            pedido=self.pedido, product=self.product,
            sku_snapshot='CAP-001', nombre_snapshot='Gorra NY',
            cantidad=1, costo_unitario=Decimal('150'), precio_unitario=Decimal('250'),
        )
        self.product.delete()
        item.refresh_from_db()
        self.assertIsNone(item.product)
        self.assertEqual(item.nombre_snapshot, 'Gorra NY')   # snapshot intacto


class CrearVentaTiendaTest(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Gorras', profit_margin=Decimal('100'))
        self.p1 = Product.objects.create(
            sku='CAP-001', name='Gorra NY', category=self.cat, base_price=Decimal('150'),
        )
        self.p2 = Product.objects.create(
            sku='CAP-002', name='Gorra LA', category=self.cat, base_price=Decimal('100'),
        )
        self.inactivo = Product.objects.create(
            sku='OLD-001', name='Viejo', category=self.cat,
            base_price=Decimal('50'), is_active=False,
        )

    def test_crea_pedido_pagado_con_items_y_pago(self):
        lineas = [
            {'sku': 'CAP-001', 'cantidad': 2, 'precio_unitario': '250'},
            {'sku': 'CAP-002', 'cantidad': 1, 'precio_unitario': '200'},
        ]
        pedido = crear_venta_tienda(lineas=lineas, cliente=None, metodo_pago='efectivo')
        self.assertEqual(pedido.origen, 'tienda')
        self.assertEqual(pedido.estado, Pedido.PAGADO)
        self.assertIsNone(pedido.cliente)
        self.assertEqual(pedido.items.count(), 2)
        self.assertEqual(pedido.precio_venta, Decimal('700'))
        self.assertEqual(pedido.costo_producto, Decimal('400'))
        self.assertEqual(pedido.ganancia, Decimal('300'))
        self.assertEqual(pedido.balance_pendiente, Decimal('0'))
        pago = pedido.pagos.get()
        self.assertEqual(pago.monto, Decimal('700'))
        self.assertEqual(pago.metodo_pago, 'efectivo')

    def test_costo_se_toma_del_servidor_no_del_cliente(self):
        lineas = [{'sku': 'CAP-001', 'cantidad': 1, 'precio_unitario': '250',
                   'costo_unitario': '0'}]
        pedido = crear_venta_tienda(lineas=lineas, cliente=None, metodo_pago='efectivo')
        self.assertEqual(pedido.items.get().costo_unitario, Decimal('150'))

    def test_descripcion_autogenerada(self):
        lineas = [{'sku': 'CAP-001', 'cantidad': 2, 'precio_unitario': '250'}]
        pedido = crear_venta_tienda(lineas=lineas, cliente=None, metodo_pago='efectivo')
        self.assertIn('Gorra NY', pedido.descripcion)
        self.assertIn('2', pedido.descripcion)

    def test_sku_inexistente_rechaza_y_no_crea_nada(self):
        lineas = [{'sku': 'NOPE', 'cantidad': 1, 'precio_unitario': '100'}]
        with self.assertRaises(VentaInvalida):
            crear_venta_tienda(lineas=lineas, cliente=None, metodo_pago='efectivo')
        self.assertEqual(Pedido.objects.count(), 0)

    def test_producto_inactivo_rechaza(self):
        lineas = [{'sku': 'OLD-001', 'cantidad': 1, 'precio_unitario': '100'}]
        with self.assertRaises(VentaInvalida):
            crear_venta_tienda(lineas=lineas, cliente=None, metodo_pago='efectivo')
        self.assertEqual(Pedido.objects.count(), 0)

    def test_cantidad_invalida_rechaza(self):
        for bad in [0, -1, 'x']:
            with self.assertRaises(VentaInvalida):
                crear_venta_tienda(
                    lineas=[{'sku': 'CAP-001', 'cantidad': bad, 'precio_unitario': '100'}],
                    cliente=None, metodo_pago='efectivo',
                )
        self.assertEqual(Pedido.objects.count(), 0)

    def test_precio_negativo_rechaza(self):
        with self.assertRaises(VentaInvalida):
            crear_venta_tienda(
                lineas=[{'sku': 'CAP-001', 'cantidad': 1, 'precio_unitario': '-5'}],
                cliente=None, metodo_pago='efectivo',
            )

    def test_lineas_vacias_rechaza(self):
        with self.assertRaises(VentaInvalida):
            crear_venta_tienda(lineas=[], cliente=None, metodo_pago='efectivo')

    def test_metodo_pago_invalido_rechaza(self):
        with self.assertRaises(VentaInvalida):
            crear_venta_tienda(
                lineas=[{'sku': 'CAP-001', 'cantidad': 1, 'precio_unitario': '100'}],
                cliente=None, metodo_pago='bitcoin',
            )

    def test_segunda_linea_invalida_revierte_todo(self):
        # línea 1 válida, línea 2 con SKU inexistente → rollback total (atomicidad real)
        lineas = [
            {'sku': 'CAP-001', 'cantidad': 1, 'precio_unitario': '250'},
            {'sku': 'NOPE', 'cantidad': 1, 'precio_unitario': '100'},
        ]
        with self.assertRaises(VentaInvalida):
            crear_venta_tienda(lineas=lineas, cliente=None, metodo_pago='efectivo')
        self.assertEqual(Pedido.objects.count(), 0)
        self.assertEqual(PedidoItem.objects.count(), 0)

    def test_cliente_se_asocia_al_pedido(self):
        cliente = Cliente.objects.create(nombre='Ana', telefono='5557778888')
        pedido = crear_venta_tienda(
            lineas=[{'sku': 'CAP-001', 'cantidad': 1, 'precio_unitario': '250'}],
            cliente=cliente, metodo_pago='efectivo',
        )
        self.assertEqual(pedido.cliente, cliente)


class PosPantallaTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('pos_ui', password='pass', is_staff=True)
        self.cat = Category.objects.create(name='Gorras', profit_margin=Decimal('100'))

    def test_pantalla_requiere_staff(self):
        resp = self.client.get('/panel/negocio/pos/')
        self.assertIn(resp.status_code, (302, 403))

    def test_pantalla_renderiza_para_staff(self):
        self.client.login(username='pos_ui', password='pass')
        resp = self.client.get('/panel/negocio/pos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Venta rápida')
        self.assertContains(resp, 'Gorras')  # categoría disponible para el filtro


import json


class PosProductosEndpointTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('pos_staff', password='pass', is_staff=True)
        self.cat_a = Category.objects.create(name='Gorras', profit_margin=Decimal('100'))
        self.cat_b = Category.objects.create(name='Tenis', profit_margin=Decimal('100'))
        Product.objects.create(sku='CAP-001', name='Gorra NY', category=self.cat_a,
                               base_price=Decimal('150'))
        Product.objects.create(sku='CAP-002', name='Gorra LA', category=self.cat_a,
                               base_price=Decimal('150'))
        Product.objects.create(sku='SNK-001', name='Air Max', category=self.cat_b,
                               base_price=Decimal('500'))
        Product.objects.create(sku='OLD-001', name='Inactivo', category=self.cat_a,
                               base_price=Decimal('10'), is_active=False)

    def test_requiere_staff(self):
        resp = self.client.get('/panel/negocio/pos/productos/')
        self.assertIn(resp.status_code, (302, 403))

    def test_lista_solo_activos(self):
        self.client.login(username='pos_staff', password='pass')
        resp = self.client.get('/panel/negocio/pos/productos/')
        data = json.loads(resp.content)
        skus = {p['sku'] for p in data['productos']}
        self.assertNotIn('OLD-001', skus)
        self.assertEqual(len(skus), 3)

    def test_busqueda_por_nombre(self):
        self.client.login(username='pos_staff', password='pass')
        resp = self.client.get('/panel/negocio/pos/productos/?q=air')
        data = json.loads(resp.content)
        self.assertEqual([p['sku'] for p in data['productos']], ['SNK-001'])

    def test_busqueda_por_sku(self):
        self.client.login(username='pos_staff', password='pass')
        resp = self.client.get('/panel/negocio/pos/productos/?q=CAP-001')
        data = json.loads(resp.content)
        self.assertEqual([p['sku'] for p in data['productos']], ['CAP-001'])

    def test_filtro_por_categoria(self):
        self.client.login(username='pos_staff', password='pass')
        resp = self.client.get(f'/panel/negocio/pos/productos/?categoria={self.cat_b.pk}')
        data = json.loads(resp.content)
        self.assertEqual([p['sku'] for p in data['productos']], ['SNK-001'])

    def test_cada_producto_trae_precio_y_nombre(self):
        self.client.login(username='pos_staff', password='pass')
        resp = self.client.get('/panel/negocio/pos/productos/?q=CAP-001')
        prod = json.loads(resp.content)['productos'][0]
        self.assertEqual(prod['nombre'], 'Gorra NY')
        # final_price = base 150 + envío 0 + margen 100 = 250
        self.assertEqual(prod['precio'], '250.00')
        self.assertIn('imagen_url', prod)
        self.assertIn('categoria_id', prod)

    def test_sin_filtro_ordena_mas_vendidos_primero(self):
        # Vende SNK-001 una vez → debe quedar primero en la vista por defecto
        pedido = Pedido.objects.create(
            descripcion='x', costo_producto=Decimal('0'), precio_venta=Decimal('0'),
            origen='tienda', estado=Pedido.PAGADO,
        )
        snk = Product.objects.get(sku='SNK-001')
        PedidoItem.objects.create(
            pedido=pedido, product=snk, sku_snapshot='SNK-001',
            nombre_snapshot='Air Max', cantidad=1,
            costo_unitario=Decimal('500'), precio_unitario=Decimal('900'),
        )
        self.client.login(username='pos_staff', password='pass')
        resp = self.client.get('/panel/negocio/pos/productos/')
        skus = [p['sku'] for p in json.loads(resp.content)['productos']]
        self.assertEqual(skus[0], 'SNK-001')  # el más vendido primero


class PosCobrarEndpointTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('cobra_staff', password='pass', is_staff=True)
        self.cat = Category.objects.create(name='Gorras', profit_margin=Decimal('100'))
        Product.objects.create(sku='CAP-001', name='Gorra NY', category=self.cat,
                               base_price=Decimal('150'))
        self.cliente = Cliente.objects.create(nombre='Ana', telefono='5552223333')

    def _post(self, payload):
        return self.client.post(
            '/panel/negocio/pos/cobrar/',
            data=json.dumps(payload), content_type='application/json',
        )

    def test_requiere_staff(self):
        resp = self._post({'lineas': [], 'metodo_pago': 'efectivo'})
        self.assertIn(resp.status_code, (302, 403))

    def test_cobro_valido_crea_venta(self):
        self.client.login(username='cobra_staff', password='pass')
        resp = self._post({
            'lineas': [{'sku': 'CAP-001', 'cantidad': 2, 'precio_unitario': '250'}],
            'cliente_id': None, 'metodo_pago': 'efectivo',
        })
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data['ok'])
        pedido = Pedido.objects.get(pk=data['pedido_id'])
        self.assertEqual(pedido.estado, Pedido.PAGADO)
        self.assertEqual(pedido.precio_venta, Decimal('500'))

    def test_cobro_con_cliente(self):
        self.client.login(username='cobra_staff', password='pass')
        resp = self._post({
            'lineas': [{'sku': 'CAP-001', 'cantidad': 1, 'precio_unitario': '250'}],
            'cliente_id': self.cliente.pk, 'metodo_pago': 'tarjeta',
        })
        data = json.loads(resp.content)
        pedido = Pedido.objects.get(pk=data['pedido_id'])
        self.assertEqual(pedido.cliente_id, self.cliente.pk)
        self.assertEqual(pedido.pagos.get().metodo_pago, 'tarjeta')

    def test_payload_invalido_no_crea_nada(self):
        self.client.login(username='cobra_staff', password='pass')
        resp = self._post({
            'lineas': [{'sku': 'NOPE', 'cantidad': 1, 'precio_unitario': '100'}],
            'metodo_pago': 'efectivo',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_json_malformado_da_400(self):
        self.client.login(username='cobra_staff', password='pass')
        resp = self.client.post(
            '/panel/negocio/pos/cobrar/', data='no-es-json',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cliente_inexistente_da_400(self):
        self.client.login(username='cobra_staff', password='pass')
        resp = self._post({
            'lineas': [{'sku': 'CAP-001', 'cantidad': 1, 'precio_unitario': '250'}],
            'cliente_id': 999999, 'metodo_pago': 'efectivo',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)


class PedidosNullClienteTest(TestCase):
    """Regresión: las ventas de tienda tienen cliente=None; las vistas de pedidos
    no deben romper (NoReverseMatch) al renderizarlas."""
    def setUp(self):
        self.staff = User.objects.create_user('plist', password='pass', is_staff=True)
        self.pedido = Pedido.objects.create(
            descripcion='Venta mostrador', costo_producto=Decimal('100'),
            precio_venta=Decimal('200'), origen='tienda', estado=Pedido.PAGADO,
        )

    def test_lista_pedidos_renderiza_con_venta_mostrador(self):
        self.client.login(username='plist', password='pass')
        resp = self.client.get('/panel/negocio/pedidos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mostrador')

    def test_detalle_pedido_renderiza_con_venta_mostrador(self):
        self.client.login(username='plist', password='pass')
        resp = self.client.get(f'/panel/negocio/pedidos/{self.pedido.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mostrador')


# ── Print utils ───────────────────────────────────────────

class PrintUtilsTest(TestCase):
    def setUp(self):
        cat = Category.objects.create(name='Gorras', slug='gorras')
        self.product = Product.objects.create(
            name='Gorra NY Azul',
            sku='GR-NY-001',
            category=cat,
            base_price=Decimal('150'),
            is_active=True,
        )
        self.cliente = Cliente.objects.create(nombre='Ana', telefono='5550000099')
        self.pedido = Pedido.objects.create(
            cliente=self.cliente,
            descripcion='Gorra NY Azul ×2',
            costo_producto=Decimal('300'),
            precio_venta=Decimal('400'),
            estado=Pedido.PAGADO,
            origen=Pedido.TIENDA,
        )
        self.item = PedidoItem.objects.create(
            pedido=self.pedido,
            product=self.product,
            sku_snapshot='GR-NY-001',
            nombre_snapshot='Gorra NY Azul',
            cantidad=2,
            costo_unitario=Decimal('150'),
            precio_unitario=Decimal('200'),
        )
        self.pago = Pago.objects.create(
            pedido=self.pedido,
            fecha=datetime.date.today(),
            monto=Decimal('400'),
            metodo_pago=Pago.EFECTIVO,
        )

    def test_build_label_json_claves_string_numericas(self):
        from negocio.print_utils import _build_label_json
        result = _build_label_json(self.product)
        self.assertIsInstance(result, dict)
        for k in result:
            self.assertTrue(k.isdigit(), f"Clave no numérica: {k}")
        self.assertIn("0", result)

    def test_build_label_json_contiene_qr_con_sku(self):
        from negocio.print_utils import _build_label_json
        result = _build_label_json(self.product)
        qr_entries = [v for v in result.values() if v.get('type') == 3]
        self.assertTrue(len(qr_entries) >= 1, "Debe haber al menos un entry QR (type=3)")
        self.assertEqual(qr_entries[0]['value'], 'GR-NY-001')

    def test_build_label_json_contiene_nombre_producto(self):
        from negocio.print_utils import _build_label_json
        result = _build_label_json(self.product)
        textos = [v.get('content', '') for v in result.values() if v.get('type') == 0]
        self.assertTrue(any('Gorra NY Azul' in t for t in textos))

    def test_build_label_json_contiene_sku_en_texto(self):
        from negocio.print_utils import _build_label_json
        result = _build_label_json(self.product)
        textos = [v.get('content', '') for v in result.values() if v.get('type') == 0]
        self.assertTrue(any('GR-NY-001' in t for t in textos))

    def test_build_receipt_json_claves_string_numericas(self):
        from negocio.print_utils import _build_receipt_json
        result = _build_receipt_json(self.pedido)
        self.assertIsInstance(result, dict)
        for k in result:
            self.assertTrue(k.isdigit(), f"Clave no numérica: {k}")

    def test_build_receipt_json_contiene_item(self):
        from negocio.print_utils import _build_receipt_json
        result = _build_receipt_json(self.pedido)
        textos = [v.get('content', '') for v in result.values() if v.get('type') == 0]
        self.assertTrue(any('Gorra NY Azul' in t for t in textos))
        self.assertTrue(any('x2' in t for t in textos))

    def test_build_receipt_json_contiene_total(self):
        from negocio.print_utils import _build_receipt_json
        result = _build_receipt_json(self.pedido)
        textos = [v.get('content', '') for v in result.values() if v.get('type') == 0]
        self.assertTrue(any('400.00' in t for t in textos))

    def test_build_receipt_json_contiene_metodo_pago(self):
        from negocio.print_utils import _build_receipt_json
        result = _build_receipt_json(self.pedido)
        textos = [v.get('content', '') for v in result.values() if v.get('type') == 0]
        self.assertTrue(any('Efectivo' in t for t in textos))
