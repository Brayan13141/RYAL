import datetime
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from negocio.models import Cliente, Pedido, Pago, Gasto, PedidoItem
from catalog.models import Category, Product, TipoArticulo, CodigoDescuento
from negocio.services import crear_venta_tienda, VentaInvalida, crear_pedido_tienda_bot


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
            urlencode({'fecha': datetime.date.today().isoformat(), 'monto': '200', 'metodo_pago': 'efectivo', 'notas': 'abono 1'}),
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
            urlencode({'fecha': datetime.date.today().isoformat(), 'monto': '350', 'metodo_pago': 'efectivo', 'notas': ''}),
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

    def test_vendido_neto_de_descuentos(self):
        """'Vendido' debe restar descuento_aplicado — mismo criterio que el
        dashboard (_stats_pedido). Antes reportaba el bruto y las dos vistas
        no cuadraban entre sí."""
        import json as _json
        cliente = Cliente.objects.create(nombre='Y', telefono='5550008888')
        Pedido.objects.create(
            cliente=cliente, descripcion='Con descuento',
            costo_producto=Decimal('400'), precio_venta=Decimal('1000'),
            descuento_aplicado=Decimal('100'), estado=Pedido.PAGADO,
        )
        res = self.client.get('/panel/negocio/')
        self.assertEqual(res.context['total_vendido'], Decimal('900'))
        self.assertEqual(res.context['total_ganancia'], Decimal('500'))
        # La tendencia de 6 meses usa el mismo criterio neto
        self.assertEqual(_json.loads(res.context['trend_vendido'])[-1], 900.0)


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

    def test_costo_respeta_base_price_override_de_subcategoria(self):
        """Con base_price_override en la subcategoría, ese ES el costo real del
        proveedor — el base_price individual del producto queda desactualizado."""
        sub = Category.objects.create(
            name='Gorras Premium', parent=self.cat,
            base_price_override=Decimal('220'),
        )
        Product.objects.create(
            sku='CAP-PRM-1', name='Gorra Premium', category=sub,
            base_price=Decimal('150'),   # viejo — el override manda
        )
        lineas = [{'sku': 'CAP-PRM-1', 'cantidad': 1, 'precio_unitario': '400'}]
        pedido = crear_venta_tienda(lineas=lineas, cliente=None, metodo_pago='efectivo')
        self.assertEqual(pedido.items.get().costo_unitario, Decimal('220'))
        self.assertEqual(pedido.costo_producto, Decimal('220'))

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

    def test_build_label_json_con_imagen_incluye_entry_tipo_1(self):
        from negocio.print_utils import _build_label_json
        result = _build_label_json(self.product, image_url='https://example.com/img.jpg')
        img_entries = [v for v in result.values() if v.get('type') == 1]
        self.assertEqual(len(img_entries), 1)
        self.assertEqual(img_entries[0]['path'], 'https://example.com/img.jpg')
        self.assertEqual(img_entries[0]['align'], 1)

    def test_build_label_json_sin_imagen_no_tiene_tipo_1(self):
        from negocio.print_utils import _build_label_json
        result = _build_label_json(self.product)
        img_entries = [v for v in result.values() if v.get('type') == 1]
        self.assertEqual(len(img_entries), 0)

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


class PrintEndpointsTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staff2', password='pass', is_staff=True)
        cat = Category.objects.create(name='Tenis', slug='tenis-print')
        self.product = Product.objects.create(
            name='Nike Air Max',
            sku='TN-AIR-001',
            category=cat,
            base_price=Decimal('350'),
            is_active=True,
        )
        self.cliente = Cliente.objects.create(nombre='Bob', telefono='5550000088')
        self.pedido = Pedido.objects.create(
            cliente=self.cliente,
            descripcion='Nike Air Max ×1',
            costo_producto=Decimal('350'),
            precio_venta=Decimal('450'),
            estado=Pedido.PAGADO,
            origen=Pedido.TIENDA,
        )
        PedidoItem.objects.create(
            pedido=self.pedido,
            product=self.product,
            sku_snapshot='TN-AIR-001',
            nombre_snapshot='Nike Air Max',
            cantidad=1,
            costo_unitario=Decimal('350'),
            precio_unitario=Decimal('450'),
        )
        Pago.objects.create(
            pedido=self.pedido,
            fecha=datetime.date.today(),
            monto=Decimal('450'),
            metodo_pago=Pago.EFECTIVO,
        )

    def _receipt_token(self, value):
        from django.core.signing import Signer
        return Signer(salt='negocio-receipt').sign(str(value))

    def _label_token(self, value):
        from django.core.signing import Signer
        return Signer(salt='negocio-label').sign(str(value))

    # ── receipt_print_json ──

    def test_receipt_token_valido_devuelve_200(self):
        token = self._receipt_token(self.pedido.pk)
        url = f'/panel/negocio/api/receipt/{self.pedido.pk}/?token={token}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('0', data)

    def test_receipt_token_invalido_devuelve_403(self):
        url = f'/panel/negocio/api/receipt/{self.pedido.pk}/?token=tampered:abc'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_receipt_sin_token_devuelve_403(self):
        url = f'/panel/negocio/api/receipt/{self.pedido.pk}/'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_receipt_pedido_inexistente_devuelve_404(self):
        token = self._receipt_token(99999)
        url = f'/panel/negocio/api/receipt/99999/?token={token}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_receipt_json_contiene_items_y_total(self):
        token = self._receipt_token(self.pedido.pk)
        url = f'/panel/negocio/api/receipt/{self.pedido.pk}/?token={token}'
        data = self.client.get(url).json()
        textos = [v.get('content', '') for v in data.values() if v.get('type') == 0]
        self.assertTrue(any('Nike Air Max' in t for t in textos))
        self.assertTrue(any('450.00' in t for t in textos))

    # ── label_print_json ──

    def test_label_token_valido_devuelve_200(self):
        token = self._label_token(self.product.sku)
        url = f'/panel/negocio/api/label/{self.product.sku}/?token={token}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('0', data)

    def test_label_qr_contiene_sku(self):
        token = self._label_token(self.product.sku)
        url = f'/panel/negocio/api/label/{self.product.sku}/?token={token}'
        data = self.client.get(url).json()
        qr = [v for v in data.values() if v.get('type') == 3]
        self.assertTrue(len(qr) >= 1)
        self.assertEqual(qr[0]['value'], 'TN-AIR-001')

    def test_label_token_invalido_devuelve_403(self):
        url = f'/panel/negocio/api/label/{self.product.sku}/?token=bad:token'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_label_sin_token_devuelve_403(self):
        url = f'/panel/negocio/api/label/{self.product.sku}/'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_label_sku_inexistente_devuelve_404(self):
        token = self._label_token('NOEXISTE-999')
        url = f'/panel/negocio/api/label/NOEXISTE-999/?token={token}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_receipt_token_de_otro_pedido_devuelve_403(self):
        otro_pedido = Pedido.objects.create(
            cliente=self.cliente,
            descripcion='Otro',
            costo_producto=Decimal('200'),
            precio_venta=Decimal('300'),
            estado=Pedido.PAGADO,
            origen=Pedido.TIENDA,
        )
        # token válido para otro_pedido, usado en URL de self.pedido
        token = self._receipt_token(otro_pedido.pk)
        url = f'/panel/negocio/api/receipt/{self.pedido.pk}/?token={token}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_label_token_de_otro_sku_devuelve_403(self):
        from catalog.models import Category, Product
        cat2 = Category.objects.create(name='Botas', slug='botas-test')
        otro_product = Product.objects.create(
            name='Bota X',
            sku='BT-X-001',
            category=cat2,
            base_price=Decimal('500'),
            is_active=True,
        )
        # token válido para otro_product.sku, usado en URL de self.product.sku
        token = self._label_token(otro_product.sku)
        url = f'/panel/negocio/api/label/{self.product.sku}/?token={token}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)


class PosCobrarBprintTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staff3', password='pass', is_staff=True)
        cat = Category.objects.create(name='Sudaderas', slug='sudaderas-bprint')
        self.product = Product.objects.create(
            name='Sudadera G5',
            sku='SD-G5-001',
            category=cat,
            base_price=Decimal('200'),
            is_active=True,
        )
        self.client.force_login(self.staff)

    def test_pos_cobrar_exitoso_incluye_bprint_url(self):
        payload = {
            'lineas': [{'sku': 'SD-G5-001', 'cantidad': 1, 'precio_unitario': '300'}],
            'cliente_id': None,
            'metodo_pago': 'efectivo',
        }
        resp = self.client.post(
            '/panel/negocio/pos/cobrar/',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertIn('bprint_url', data)
        self.assertTrue(data['bprint_url'].startswith('bprint://'))
        self.assertIn('/negocio/api/receipt/', data['bprint_url'])
        self.assertIn('token=', data['bprint_url'])


class PosProductosLabelUrlTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staff4', password='pass', is_staff=True)
        cat = Category.objects.create(name='Gorras2', slug='gorras2-label')
        Product.objects.create(
            name='Gorra Chicago',
            sku='GR-CH-001',
            category=cat,
            base_price=Decimal('120'),
            is_active=True,
        )
        self.client.force_login(self.staff)

    def test_pos_productos_incluye_label_bprint_url(self):
        resp = self.client.get('/panel/negocio/pos/productos/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(len(data['productos']) >= 1)
        from urllib.parse import urlparse, parse_qs
        from django.core.signing import Signer
        for p in data['productos']:
            self.assertIn('label_bprint_url', p)
            self.assertTrue(p['label_bprint_url'].startswith('bprint://'))
            self.assertIn('/negocio/api/label/', p['label_bprint_url'])
            self.assertIn('token=', p['label_bprint_url'])
            # Verifica que el token firma el SKU correcto
            inner_url = p['label_bprint_url'].split('bprint://', 1)[1]
            parsed = urlparse(inner_url)
            token = parse_qs(parsed.query)['token'][0]
            self.assertEqual(Signer(salt='negocio-label').unsign(token), p['sku'])


class CrearPedidoBotTest(TestCase):
    def test_crea_pedido_pagado_origen_bot(self):
        # Los pedidos creados por el bot se registran directamente como Pagado
        from negocio.services import crear_pedido_bot
        pedido = crear_pedido_bot(
            nombre='Bryan', telefono='5512345678',
            items=[{'description': 'Gorra azul $500 MXN', 'price': 500, 'qty': 1}],
            envio=Decimal('0'),
        )
        self.assertEqual(pedido.origen, 'bot')
        self.assertEqual(pedido.estado, Pedido.PAGADO)
        self.assertEqual(pedido.precio_venta, Decimal('500'))
        self.assertEqual(pedido.costo_producto, Decimal('0'))

    def test_crea_cliente_si_no_existe(self):
        from negocio.services import crear_pedido_bot
        crear_pedido_bot(
            nombre='Nuevo', telefono='5599991111',
            items=[{'description': 'Tenis', 'price': 800, 'qty': 1}],
            envio=Decimal('0'),
        )
        self.assertTrue(Cliente.objects.filter(telefono='5599991111').exists())

    def test_reutiliza_cliente_existente(self):
        from negocio.services import crear_pedido_bot
        Cliente.objects.create(nombre='Ya existe', telefono='5512345678')
        crear_pedido_bot(
            nombre='Nombre nuevo', telefono='5512345678',
            items=[{'description': 'x', 'price': 100, 'qty': 1}],
            envio=Decimal('0'),
        )
        self.assertEqual(Cliente.objects.filter(telefono='5512345678').count(), 1)

    def test_crea_pedido_item_sku_bot(self):
        from negocio.services import crear_pedido_bot
        pedido = crear_pedido_bot(
            nombre='Bryan', telefono='5512345678',
            items=[{'description': 'Gorra azul', 'price': 500, 'qty': 2}],
            envio=Decimal('0'),
        )
        item = pedido.items.get()
        self.assertEqual(item.sku_snapshot, 'BOT')
        self.assertIsNone(item.product)
        self.assertEqual(item.cantidad, 2)
        self.assertEqual(item.precio_unitario, Decimal('500'))
        self.assertEqual(item.costo_unitario, Decimal('0'))

    def test_precio_venta_es_suma_de_items(self):
        from negocio.services import crear_pedido_bot
        pedido = crear_pedido_bot(
            nombre='Bryan', telefono='5512345678',
            items=[
                {'description': 'A', 'price': 500, 'qty': 2},
                {'description': 'B', 'price': 300, 'qty': 1},
            ],
            envio=Decimal('50'),
        )
        self.assertEqual(pedido.precio_venta, Decimal('1300'))  # 500*2 + 300*1
        self.assertEqual(pedido.envio, Decimal('50'))
        self.assertEqual(pedido.total_a_cobrar, Decimal('1350'))

    def test_items_vacios_rechaza(self):
        from negocio.services import crear_pedido_bot, VentaInvalida
        with self.assertRaises(VentaInvalida):
            crear_pedido_bot(
                nombre='Bryan', telefono='5512345678',
                items=[], envio=Decimal('0'),
            )
        self.assertEqual(Pedido.objects.count(), 0)

    def test_normaliza_telefono(self):
        from negocio.services import crear_pedido_bot
        crear_pedido_bot(
            nombre='Bryan', telefono='521 55 1234 5678',
            items=[{'description': 'x', 'price': 100, 'qty': 1}],
            envio=Decimal('0'),
        )
        self.assertTrue(Cliente.objects.filter(telefono='5512345678').exists())


@override_settings(NEGOCIO_API_KEY='test-key-123')
class ApiPedidoCreateTest(TestCase):
    def _post(self, payload, key='test-key-123'):
        import json
        headers = {'HTTP_AUTHORIZATION': f'Bearer {key}'} if key else {}
        return self.client.post(
            '/api/negocio/pedido/',
            data=json.dumps(payload),
            content_type='application/json',
            **headers,
        )

    def test_crea_pedido_exitosamente(self):
        res = self._post({
            'nombre': 'Bryan',
            'telefono': '5512345678',
            'items': [{'description': 'Gorra azul', 'price': 500, 'qty': 1}],
            'envio': 0,
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        self.assertIn('pedido_id', data)
        self.assertIn('total', data)
        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(Pedido.objects.get().origen, 'bot')

    def test_sin_api_key_devuelve_401(self):
        res = self._post({'nombre': 'x', 'telefono': '5512345678', 'items': [], 'envio': 0}, key=None)
        self.assertEqual(res.status_code, 401)

    def test_api_key_incorrecta_devuelve_401(self):
        res = self._post({'nombre': 'x', 'telefono': '5512345678', 'items': [], 'envio': 0}, key='wrong')
        self.assertEqual(res.status_code, 401)

    def test_items_vacios_devuelve_400(self):
        res = self._post({'nombre': 'Bryan', 'telefono': '5512345678', 'items': [], 'envio': 0})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_sin_nombre_devuelve_400(self):
        res = self._post({'nombre': '', 'telefono': '5512345678',
                         'items': [{'description': 'x', 'price': 100, 'qty': 1}], 'envio': 0})
        self.assertEqual(res.status_code, 400)

    def test_json_malformado_devuelve_400(self):
        res = self.client.post(
            '/api/negocio/pedido/',
            data='no-json',
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer test-key-123',
        )
        self.assertEqual(res.status_code, 400)

    def test_total_correcto_con_envio(self):
        res = self._post({
            'nombre': 'Bryan',
            'telefono': '5512345678',
            'items': [{'description': 'Gorra', 'price': 500, 'qty': 2}],
            'envio': 100,
        })
        data = res.json()
        self.assertEqual(data['total'], '1100.00')  # 500*2 + 100 envio


class PedidoDetailBotTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('bot_staff', password='pass', is_staff=True)
        self.cliente = Cliente.objects.create(nombre='Bryan', telefono='5512345678')
        self.pedido = Pedido.objects.create(
            cliente=self.cliente,
            descripcion='Bot: Gorra azul ×2',
            costo_producto=Decimal('0'),
            precio_venta=Decimal('1000'),
            envio=Decimal('100'),
            estado=Pedido.PENDIENTE,
            origen=Pedido.BOT,
        )
        PedidoItem.objects.create(
            pedido=self.pedido,
            product=None,
            sku_snapshot='BOT',
            nombre_snapshot='Gorra azul $500 MXN',
            cantidad=2,
            costo_unitario=Decimal('0'),
            precio_unitario=Decimal('500'),
        )
        self.client.login(username='bot_staff', password='pass')

    def test_pedido_detail_muestra_badge_bot(self):
        res = self.client.get(f'/panel/negocio/pedidos/{self.pedido.pk}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Bot')

    def test_pedido_detail_muestra_items(self):
        res = self.client.get(f'/panel/negocio/pedidos/{self.pedido.pk}/')
        self.assertContains(res, 'Gorra azul $500 MXN')

    def test_pedido_detail_muestra_cantidad(self):
        res = self.client.get(f'/panel/negocio/pedidos/{self.pedido.pk}/')
        self.assertContains(res, '500')  # precio unitario


@override_settings(NEGOCIO_API_KEY='test-key-123')
class ApiClientesBuscarTest(TestCase):
    def setUp(self):
        self.ana   = Cliente.objects.create(nombre='Ana López',  telefono='5551111111')
        self.pedro = Cliente.objects.create(nombre='Pedro Ríos', telefono='5552222222')
        self.ana2  = Cliente.objects.create(nombre='Ana García', telefono='5553333333')

    def _get(self, q, key='test-key-123'):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {key}'} if key else {}
        return self.client.get(f'/api/negocio/clientes/buscar/?q={q}', **headers)

    def test_busca_por_nombre_exacto(self):
        res = self._get('Pedro')
        self.assertEqual(res.status_code, 200)
        data = res.json()['clientes']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['nombre'], 'Pedro Ríos')
        self.assertEqual(data[0]['telefono'], '5552222222')
        self.assertIn('id', data[0])
        self.assertIn('descuento', data[0])

    def test_busca_por_nombre_parcial_devuelve_varios(self):
        res = self._get('Ana')
        self.assertEqual(res.status_code, 200)
        data = res.json()['clientes']
        self.assertEqual(len(data), 2)

    def test_busca_por_telefono(self):
        res = self._get('5551111111')
        self.assertEqual(res.status_code, 200)
        data = res.json()['clientes']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['nombre'], 'Ana López')

    def test_busca_por_telefono_jid_521(self):
        res = self._get('5215551111111')
        self.assertEqual(res.status_code, 200)
        data = res.json()['clientes']
        self.assertEqual(len(data), 1)

    def test_sin_resultados_devuelve_lista_vacia(self):
        res = self._get('ZzzNadaNada999')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['clientes'], [])

    def test_q_vacio_devuelve_lista_vacia(self):
        res = self._get('')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['clientes'], [])

    def test_sin_api_key_devuelve_401(self):
        res = self._get('Ana', key=None)
        self.assertEqual(res.status_code, 401)

    def test_api_key_incorrecta_devuelve_401(self):
        res = self._get('Ana', key='wrong')
        self.assertEqual(res.status_code, 401)


from decimal import Decimal


class CrearPedidoTiendaBotTest(TestCase):
    def test_crea_pedido_origen_tienda(self):
        items = [{'description': 'tenis rojo', 'price': 450, 'qty': 2}]
        pedido = crear_pedido_tienda_bot(items=items)
        self.assertEqual(pedido.origen, Pedido.TIENDA)
        # Las ventas de tienda se registran directamente como Pagado
        self.assertEqual(pedido.estado, Pedido.PAGADO)
        self.assertEqual(pedido.precio_venta, Decimal('900'))
        self.assertEqual(pedido.costo_producto, Decimal('0'))

    def test_crea_cliente_mostrador_get_or_create(self):
        items = [{'description': 'gorra', 'price': 200, 'qty': 1}]
        crear_pedido_tienda_bot(items=items)
        crear_pedido_tienda_bot(items=items)
        from negocio.services import MOSTRADOR_TELEFONO
        self.assertEqual(Cliente.objects.filter(telefono=MOSTRADOR_TELEFONO).count(), 1)

    def test_pedido_items_creados(self):
        items = [
            {'description': 'tenis', 'price': 450, 'qty': 2},
            {'description': 'gorra', 'price': 200, 'qty': 1},
        ]
        pedido = crear_pedido_tienda_bot(items=items)
        self.assertEqual(pedido.items.count(), 2)
        item1 = pedido.items.order_by('precio_unitario').last()
        self.assertEqual(item1.precio_unitario, Decimal('450'))
        self.assertEqual(item1.cantidad, 2)
        self.assertIsNone(item1.product)
        self.assertEqual(item1.sku_snapshot, 'TIENDA-BOT')

    def test_item_sin_descripcion_usa_fallback(self):
        items = [{'description': '', 'price': 300, 'qty': 1}]
        pedido = crear_pedido_tienda_bot(items=items)
        item = pedido.items.first()
        self.assertEqual(item.nombre_snapshot, 'ítem tienda')

    def test_envio_incluido_en_pedido(self):
        items = [{'description': 'tenis', 'price': 400, 'qty': 1}]
        pedido = crear_pedido_tienda_bot(items=items, envio=Decimal('80'))
        self.assertEqual(pedido.envio, Decimal('80'))

    def test_lista_vacia_lanza_error(self):
        from negocio.services import VentaInvalida
        with self.assertRaises(VentaInvalida):
            crear_pedido_tienda_bot(items=[])


@override_settings(NEGOCIO_API_KEY='test-key-123')
class ApiTiendaCreateTest(TestCase):
    def _post(self, body, key='test-key-123'):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {key}', 'content_type': 'application/json'}
        return self.client.post('/api/negocio/tienda/', data=json.dumps(body), **headers)

    def test_crea_pedido_y_devuelve_id(self):
        res = self._post({'items': [{'description': 'tenis', 'price': 450, 'qty': 2}]})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        self.assertIn('pedido_id', data)
        self.assertEqual(data['total'], '900.00')

    def test_sin_api_key_devuelve_401(self):
        res = self._post({'items': [{'description': 'x', 'price': 100, 'qty': 1}]}, key=None)
        self.assertEqual(res.status_code, 401)

    def test_items_vacios_devuelve_400(self):
        res = self._post({'items': []})
        self.assertEqual(res.status_code, 400)

    def test_con_envio(self):
        res = self._post({'items': [{'description': 'tenis', 'price': 400, 'qty': 1}], 'envio': 80})
        self.assertEqual(res.status_code, 200)
        pedido = Pedido.objects.get(pk=res.json()['pedido_id'])
        self.assertEqual(pedido.envio, Decimal('80'))


class PedidoDescuentoPropertyTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Test', telefono='5550000001')
        self.pedido = Pedido.objects.create(
            cliente=self.cliente,
            costo_producto=Decimal('300'),
            precio_venta=Decimal('500'),
            envio=Decimal('50'),
            descuento_aplicado=Decimal('50'),
        )

    def test_total_a_cobrar_resta_descuento(self):
        # 500 + 50 - 50 = 500
        self.assertEqual(self.pedido.total_a_cobrar, Decimal('500'))

    def test_ganancia_resta_descuento(self):
        # 500 - 300 - 50 = 150
        self.assertEqual(self.pedido.ganancia, Decimal('150'))

    def test_sin_descuento_ganancia_original(self):
        self.pedido.descuento_aplicado = Decimal('0')
        self.assertEqual(self.pedido.ganancia, Decimal('200'))


@override_settings(NEGOCIO_API_KEY='test-key-123')
class ApiTiposListTest(TestCase):
    def setUp(self):
        self.tipo = TipoArticulo.objects.create(
            nombre='Gorras', keywords='gorra,cap', costo=Decimal('280')
        )

    def test_lista_tipos(self):
        r = self.client.get(
            '/api/negocio/tipos/',
            HTTP_AUTHORIZATION=f'Bearer {settings.NEGOCIO_API_KEY}',
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data['tipos']), 1)
        self.assertEqual(data['tipos'][0]['nombre'], 'Gorras')

    def test_sin_api_key_devuelve_401(self):
        r = self.client.get('/api/negocio/tipos/')
        self.assertEqual(r.status_code, 401)


@override_settings(NEGOCIO_API_KEY='test-key-123')
class ApiArticuloBuscarTest(TestCase):
    def setUp(self):
        TipoArticulo.objects.create(nombre='Gorras', keywords='gorra,cap,ny', costo=Decimal('280'))

    def test_encuentra_tipo(self):
        r = self.client.post(
            '/api/negocio/articulo/buscar/',
            data='{"descripcion": "Gorra NY negra"}',
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {settings.NEGOCIO_API_KEY}',
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['match'])
        self.assertEqual(data['nombre'], 'Gorras')
        self.assertEqual(data['costo'], 280.0)

    def test_sin_match(self):
        r = self.client.post(
            '/api/negocio/articulo/buscar/',
            data='{"descripcion": "Tenis Nike"}',
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {settings.NEGOCIO_API_KEY}',
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['match'])

    def test_sin_api_key_devuelve_401(self):
        r = self.client.post('/api/negocio/articulo/buscar/', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 401)


@override_settings(NEGOCIO_API_KEY='test-key-123')
class ApiCodigosValidarTest(TestCase):
    def setUp(self):
        CodigoDescuento.objects.create(codigo='TEST50', descuento=Decimal('50'), is_active=True)

    def test_codigo_valido(self):
        r = self.client.post(
            '/api/negocio/codigos/validar/',
            data='{"codigo": "TEST50", "descriptions": []}',
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {settings.NEGOCIO_API_KEY}',
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['valido'])
        self.assertEqual(data['descuento'], 50.0)

    def test_codigo_invalido(self):
        r = self.client.post(
            '/api/negocio/codigos/validar/',
            data='{"codigo": "NOEXISTE", "descriptions": []}',
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {settings.NEGOCIO_API_KEY}',
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['valido'])

    def test_sin_codigo_devuelve_400(self):
        r = self.client.post(
            '/api/negocio/codigos/validar/',
            data='{"descriptions": []}',
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {settings.NEGOCIO_API_KEY}',
        )
        self.assertEqual(r.status_code, 400)


@override_settings(NEGOCIO_API_KEY='test-key-123')
class CrearPedidoBotConCostoTest(TestCase):
    def setUp(self):
        self.url = '/api/negocio/pedido/'
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {settings.NEGOCIO_API_KEY}'}

    def _post(self, payload):
        import json
        return self.client.post(
            self.url, data=json.dumps(payload),
            content_type='application/json', **self.auth
        )

    def test_costo_por_item_se_guarda(self):
        r = self._post({
            'nombre': 'Ana', 'telefono': '5551111111',
            'items': [{'description': 'Gorra NY', 'price': 450, 'qty': 1, 'costo': 350}],
        })
        self.assertEqual(r.status_code, 200)
        from negocio.models import PedidoItem
        item = PedidoItem.objects.get(nombre_snapshot='Gorra NY')
        self.assertEqual(item.costo_unitario, Decimal('350'))

    def test_costo_producto_total_en_pedido(self):
        r = self._post({
            'nombre': 'Ana', 'telefono': '5551112222',
            'items': [
                {'description': 'Gorra', 'price': 450, 'qty': 2, 'costo': 350},
                {'description': 'Cap',   'price': 400, 'qty': 1, 'costo': 300},
            ],
        })
        self.assertEqual(r.status_code, 200)
        from negocio.models import Pedido
        pedido = Pedido.objects.get(cliente__telefono='5551112222')
        # 350*2 + 300*1 = 1000
        self.assertEqual(pedido.costo_producto, Decimal('1000'))

    def test_descuento_se_aplica(self):
        code = CodigoDescuento.objects.create(codigo='DESC100', descuento=Decimal('100'), is_active=True)
        r = self._post({
            'nombre': 'Luis', 'telefono': '5553333333',
            'items': [{'description': 'Gorra NY', 'price': 500, 'qty': 1, 'costo': 400}],
            'descuento_monto': 100,
            'codigo_descuento_id': code.pk,
        })
        self.assertEqual(r.status_code, 200)
        from negocio.models import Pedido
        pedido = Pedido.objects.get(cliente__telefono='5553333333')
        self.assertEqual(pedido.descuento_aplicado, Decimal('100'))
        self.assertEqual(pedido.codigo_descuento, code)
        # ganancia = 500 - 400 - 100 = 0
        self.assertEqual(pedido.ganancia, Decimal('0'))

    def test_descuento_incrementa_usos(self):
        code = CodigoDescuento.objects.create(
            codigo='USOTEST', descuento=Decimal('50'), is_active=True, usos_actuales=3
        )
        self._post({
            'nombre': 'Pedro', 'telefono': '5554444444',
            'items': [{'description': 'Cap', 'price': 400, 'qty': 1, 'costo': 300}],
            'descuento_monto': 50, 'codigo_descuento_id': code.pk,
        })
        code.refresh_from_db()
        self.assertEqual(code.usos_actuales, 4)

    def test_sin_costo_usa_cero(self):
        r = self._post({
            'nombre': 'Rosa', 'telefono': '5555555555',
            'items': [{'description': 'Artículo', 'price': 300, 'qty': 1}],
        })
        self.assertEqual(r.status_code, 200)
        from negocio.models import PedidoItem
        item = PedidoItem.objects.get(nombre_snapshot='Artículo')
        self.assertEqual(item.costo_unitario, Decimal('0'))


class CrearPedidoBotDescuentoCapTest(TestCase):
    """El descuento del bot se capea al total (precio + envío) — sin tope,
    total_a_cobrar y ganancia quedaban negativos en Django aunque el bot
    mostrara $0 en WhatsApp (computeTotal ya floorea del lado JS)."""

    def test_descuento_mayor_al_total_se_capea(self):
        from negocio.services import crear_pedido_bot
        pedido = crear_pedido_bot(
            nombre='Ana', telefono='5512345678',
            items=[{'description': 'Tenis', 'price': '500', 'qty': 1, 'costo': '400'}],
            envio=Decimal('50'),
            descuento_aplicado=Decimal('10000'),
        )
        self.assertEqual(pedido.descuento_aplicado, Decimal('550'))
        self.assertEqual(pedido.total_a_cobrar, Decimal('0'))


@override_settings(RATELIMIT_ENABLE=False)
class PedidosListFiltroFechaTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('s', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.cli = Cliente.objects.create(nombre='N', telefono='9')

    def _ped(self, f):
        return Pedido.objects.create(cliente=self.cli, costo_producto=Decimal('0'),
                                     precio_venta=Decimal('100'), estado=Pedido.PAGADO, fecha=f)

    def test_filtra_por_rango_inclusivo(self):
        self._ped(datetime.date(2026, 1, 10))
        self._ped(datetime.date(2026, 2, 15))
        self._ped(datetime.date(2026, 3, 20))
        res = self.client.get('/panel/negocio/pedidos/?desde=2026-02-01&hasta=2026-02-28')
        self.assertEqual(len(res.context['pedidos']), 1)

    def test_sin_filtro_muestra_todos(self):
        self._ped(datetime.date(2026, 1, 10)); self._ped(datetime.date(2026, 2, 15))
        res = self.client.get('/panel/negocio/pedidos/')
        self.assertEqual(len(res.context['pedidos']), 2)


class PedidosListFiltroEstadoYCardsTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('s6', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.cli = Cliente.objects.create(nombre='N', telefono='9')

    def _ped(self, estado, precio='100', fecha=None, pagado=None):
        ped = Pedido.objects.create(
            cliente=self.cli, costo_producto=Decimal('0'),
            precio_venta=Decimal(precio), estado=estado,
            fecha=fecha or datetime.date(2026, 7, 1),
        )
        if pagado is not None:
            Pago.objects.create(pedido=ped, fecha=ped.fecha, monto=Decimal(pagado))
        return ped

    def test_filtro_estado_pendiente_solo_muestra_pendientes(self):
        self._ped(Pedido.PENDIENTE)
        self._ped(Pedido.PAGADO)
        res = self.client.get('/panel/negocio/pedidos/?estado=pendiente')
        self.assertEqual(len(res.context['pedidos']), 1)
        self.assertEqual(res.context['pedidos'][0].estado, Pedido.PENDIENTE)

    def test_filtro_estado_pagado_solo_muestra_pagados(self):
        self._ped(Pedido.PENDIENTE)
        self._ped(Pedido.PAGADO)
        res = self.client.get('/panel/negocio/pedidos/?estado=pagado')
        self.assertEqual(len(res.context['pedidos']), 1)
        self.assertEqual(res.context['pedidos'][0].estado, Pedido.PAGADO)

    def test_sin_filtro_estado_muestra_todos(self):
        self._ped(Pedido.PENDIENTE)
        self._ped(Pedido.PAGADO)
        self._ped(Pedido.CANCELADO)
        res = self.client.get('/panel/negocio/pedidos/')
        self.assertEqual(len(res.context['pedidos']), 3)

    def test_cards_globales_no_cambian_con_filtro_fecha(self):
        # Pedido pendiente FUERA del rango desde/hasta que se va a pedir.
        self._ped(Pedido.PENDIENTE, precio='500', fecha=datetime.date(2026, 1, 1))
        res = self.client.get('/panel/negocio/pedidos/?desde=2026-07-01&hasta=2026-07-31')
        self.assertEqual(len(res.context['pedidos']), 0)  # el filtro de fecha sí lo excluye de la tabla
        self.assertEqual(res.context['n_pendientes_global'], 1)  # pero la card lo sigue contando
        self.assertEqual(res.context['total_por_cobrar_global'], Decimal('500'))

    def test_cards_cuentan_pagos_parciales(self):
        self._ped(Pedido.PENDIENTE, precio='1000', pagado='300')
        self._ped(Pedido.PENDIENTE, precio='200')
        self._ped(Pedido.PAGADO, precio='999')  # no debe contar, está pagado
        res = self.client.get('/panel/negocio/pedidos/')
        self.assertEqual(res.context['n_pendientes_global'], 2)
        self.assertEqual(res.context['total_por_cobrar_global'], Decimal('900'))  # (1000-300) + 200

    def test_estado_choices_en_contexto(self):
        res = self.client.get('/panel/negocio/pedidos/')
        valores = [v for v, _ in res.context['estado_choices']]
        self.assertEqual(valores, ['pendiente', 'pagado', 'cancelado'])


@override_settings(RATELIMIT_ENABLE=False)
class GastosListFiltroFechaTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('s5', password='x', is_staff=True)
        self.client.force_login(self.staff)

    def _gasto(self, f):
        return Gasto.objects.create(fecha=f, descripcion='x', monto=Decimal('50'))

    def test_filtra_gastos_por_rango(self):
        self._gasto(datetime.date(2026, 1, 5)); self._gasto(datetime.date(2026, 2, 5))
        res = self.client.get('/panel/negocio/gastos/?desde=2026-02-01&hasta=2026-02-28')
        self.assertEqual(len(res.context['gastos']), 1)


@override_settings(RATELIMIT_ENABLE=False)
class PagosListTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('s4', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.cli = Cliente.objects.create(nombre='N', telefono='9')
        self.ped = Pedido.objects.create(cliente=self.cli, costo_producto=Decimal('0'),
                                         precio_venta=Decimal('1000'), estado=Pedido.PAGADO,
                                         fecha=datetime.date(2026, 2, 10))

    def _pago(self, f, monto, metodo='efectivo'):
        return Pago.objects.create(pedido=self.ped, fecha=f, monto=Decimal(monto), metodo_pago=metodo)

    def test_lista_total_y_por_metodo_en_rango(self):
        self._pago(datetime.date(2026, 1, 5), '100', 'efectivo')          # fuera
        self._pago(datetime.date(2026, 2, 5), '300', 'efectivo')          # dentro
        self._pago(datetime.date(2026, 2, 6), '200', 'transferencia')     # dentro
        res = self.client.get('/panel/negocio/pagos/?desde=2026-02-01&hasta=2026-02-28')
        self.assertEqual(res.context['total'], Decimal('500'))
        self.assertEqual(len(res.context['pagos']), 2)
        metodos = {r['metodo']: r['total'] for r in res.context['por_metodo']}
        self.assertEqual(metodos['efectivo'], Decimal('300'))
        self.assertEqual(metodos['transferencia'], Decimal('200'))


@override_settings(RATELIMIT_ENABLE=False)
class CajaAjusteTests(TestCase):
    """Saldo real de caja = cobrado - gastos + ajustes. Opcion A: el usuario
    escribe el total real contado y el sistema guarda la diferencia como ajuste
    con motivo, usuario y saldo resultante (auditoria)."""

    def setUp(self):
        self.staff = User.objects.create_user('cajero', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.cli = Cliente.objects.create(nombre='N', telefono='9')

    def _pago(self, monto):
        ped = Pedido.objects.create(cliente=self.cli, costo_producto=Decimal('0'),
                                    precio_venta=Decimal(monto), estado=Pedido.PAGADO,
                                    fecha=datetime.date.today())
        Pago.objects.create(pedido=ped, fecha=datetime.date.today(),
                            monto=Decimal(monto), metodo_pago='efectivo')

    def test_caja_totales_incluye_ajustes(self):
        from negocio.caja import caja_totales
        from negocio.models import AjusteCaja
        self._pago('200')
        Gasto.objects.create(fecha=datetime.date.today(), descripcion='x', monto=Decimal('50'))
        AjusteCaja.objects.create(fecha=datetime.date.today(), monto=Decimal('5000'),
                                  saldo_resultante=Decimal('5150'), motivo='Saldo inicial')
        t = caja_totales()
        self.assertEqual(t['cobrado'], Decimal('200'))
        self.assertEqual(t['gastos'], Decimal('50'))
        self.assertEqual(t['ajustes'], Decimal('5000'))
        self.assertEqual(t['saldo'], Decimal('5150'))   # 200 - 50 + 5000

    def test_arqueo_crea_ajuste_por_diferencia(self):
        from negocio.caja import caja_totales
        from negocio.models import AjusteCaja
        self._pago('300')   # saldo calculado = 300
        res = self.client.post('/panel/negocio/caja/',
                               {'total_real': '8000', 'motivo': 'Saldo inicial'})
        self.assertEqual(res.status_code, 302)
        aj = AjusteCaja.objects.get()
        self.assertEqual(aj.monto, Decimal('7700'))            # 8000 - 300
        self.assertEqual(aj.saldo_resultante, Decimal('8000'))
        self.assertEqual(aj.motivo, 'Saldo inicial')
        self.assertEqual(aj.usuario, self.staff)
        self.assertEqual(caja_totales()['saldo'], Decimal('8000'))

    def test_arqueo_exige_motivo(self):
        from negocio.models import AjusteCaja
        res = self.client.post('/panel/negocio/caja/',
                               {'total_real': '5000', 'motivo': ''})
        self.assertEqual(res.status_code, 200)                 # re-render con error
        self.assertEqual(AjusteCaja.objects.count(), 0)

    def test_arqueo_total_invalido_no_crea(self):
        from negocio.models import AjusteCaja
        res = self.client.post('/panel/negocio/caja/',
                               {'total_real': 'abc', 'motivo': 'x'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(AjusteCaja.objects.count(), 0)

    def test_caja_page_lista_ajustes(self):
        from negocio.models import AjusteCaja
        AjusteCaja.objects.create(fecha=datetime.date.today(), monto=Decimal('5000'),
                                  saldo_resultante=Decimal('5000'), motivo='Saldo inicial')
        res = self.client.get('/panel/negocio/caja/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Saldo inicial')


class RankingPorTipoTests(TestCase):
    """`ranking_por_tipo` agrupa las ventas del negocio por TipoArticulo.

    El texto de una venta es libre y ruidoso ('gorra', 'gorras', 'gorra
    barbas' son lo mismo), y no hay FK a Product en ninguna línea: el tipo es
    la única unidad de agrupación confiable que existe.
    """

    def setUp(self):
        self.cli = Cliente.objects.create(nombre='N', telefono='9')
        self.gorras = TipoArticulo.objects.create(
            nombre='Gorras', keywords='gorra,gorras', costo=Decimal('240'))
        self.tenis = TipoArticulo.objects.create(
            nombre='Tenis', keywords='tenis', costo=Decimal('600'))

    def _pedido(self, fecha=None, estado=Pedido.PAGADO, descuento='0', descripcion=''):
        return Pedido.objects.create(
            cliente=self.cli, descripcion=descripcion,
            costo_producto=Decimal('0'), precio_venta=Decimal('0'),
            descuento_aplicado=Decimal(descuento), estado=estado,
            fecha=fecha or datetime.date(2026, 7, 15),
        )

    def _item(self, pedido, nombre, cantidad=1, precio='300', costo='240'):
        it = PedidoItem.objects.create(
            pedido=pedido, sku_snapshot='TIENDA-BOT', nombre_snapshot=nombre,
            cantidad=cantidad, costo_unitario=Decimal(costo),
            precio_unitario=Decimal(precio),
        )
        # precio_venta del pedido = suma de sus líneas, como lo deja el bot.
        pedido.precio_venta = sum(i.precio_unitario * i.cantidad for i in pedido.items.all())
        pedido.save(update_fields=['precio_venta'])
        return it

    def _fila(self, filas, nombre):
        return next(f for f in filas if f['tipo'] == nombre)

    def test_agrupa_variantes_de_texto_en_el_mismo_tipo(self):
        """'gorra' y 'gorras' son el mismo producto: deben sumar juntos."""
        from negocio.services import ranking_por_tipo
        p = self._pedido()
        self._item(p, 'gorra', cantidad=1)
        self._item(p, 'gorras', cantidad=10)
        filas = ranking_por_tipo()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['tipo'], 'Gorras')
        self.assertEqual(filas[0]['piezas'], 11)

    def test_ordena_por_piezas_descendente(self):
        from negocio.services import ranking_por_tipo
        p = self._pedido()
        self._item(p, 'tenis', cantidad=2)
        self._item(p, 'gorras', cantidad=9)
        filas = ranking_por_tipo()
        self.assertEqual([f['tipo'] for f in filas], ['Gorras', 'Tenis'])

    def test_ganancia_es_ingreso_menos_costo_grabado(self):
        from negocio.services import ranking_por_tipo
        p = self._pedido()
        self._item(p, 'gorras', cantidad=2, precio='300', costo='240')
        fila = ranking_por_tipo()[0]
        self.assertEqual(fila['ingreso'], Decimal('600'))
        self.assertEqual(fila['costo'], Decimal('480'))
        self.assertEqual(fila['ganancia'], Decimal('120'))

    def test_el_total_del_ranking_es_el_total_del_dashboard(self):
        """El invariante que importa: si el reporte no suma lo mismo que el
        dashboard, uno de los dos miente y no se sabe cuál."""
        from negocio.services import ranking_por_tipo
        from negocio.utils import _VENDIDO_EXPR
        from django.db.models import Sum
        p1 = self._pedido()
        self._item(p1, 'gorras', cantidad=3, precio='300')
        p2 = self._pedido()
        self._item(p2, 'tenis', cantidad=1, precio='900', costo='600')
        p3 = self._pedido(descripcion='1 tenis')
        p3.precio_venta = Decimal('500')
        p3.costo_producto = Decimal('300')
        p3.save()

        del_dashboard = Pedido.objects.filter(estado=Pedido.PAGADO).aggregate(
            v=Sum(_VENDIDO_EXPR))['v']
        del_ranking = sum(f['ingreso'] for f in ranking_por_tipo())
        self.assertEqual(del_ranking, del_dashboard)

    def test_el_descuento_del_pedido_se_prorratea_entre_sus_lineas(self):
        """Hoy no hay ni un descuento aplicado en producción. El día que lo
        haya, el ranking no puede despegarse del dashboard en silencio."""
        from negocio.services import ranking_por_tipo
        from negocio.utils import _VENDIDO_EXPR
        from django.db.models import Sum
        p = self._pedido(descuento='100')
        self._item(p, 'gorras', cantidad=1, precio='300')
        self._item(p, 'tenis', cantidad=1, precio='700', costo='600')

        del_dashboard = Pedido.objects.filter(estado=Pedido.PAGADO).aggregate(
            v=Sum(_VENDIDO_EXPR))['v']
        filas = ranking_por_tipo()
        self.assertEqual(sum(f['ingreso'] for f in filas), del_dashboard)
        # El descuento pesa proporcionalmente: 30% del precio para gorras.
        self.assertEqual(self._fila(filas, 'Gorras')['ingreso'], Decimal('270'))
        self.assertEqual(self._fila(filas, 'Tenis')['ingreso'], Decimal('630'))

    def test_pedido_sin_lineas_entra_por_su_descripcion(self):
        """Los 7 pedidos capturados a mano no tienen PedidoItem: su tipo sale
        de la descripción y cuentan 1 pieza (no hay campo de cantidad)."""
        from negocio.services import ranking_por_tipo
        p = self._pedido(descripcion='3 gorras Barbas')
        p.precio_venta = Decimal('500')
        p.costo_producto = Decimal('240')
        p.save()
        filas = ranking_por_tipo()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['tipo'], 'Gorras')
        self.assertEqual(filas[0]['piezas'], 1)
        self.assertEqual(filas[0]['ingreso'], Decimal('500'))
        self.assertEqual(filas[0]['sin_desglose'], 1)

    def test_lo_que_no_matchea_ningun_tipo_es_visible(self):
        """Esconder lo no clasificado haría que los totales no cuadren sin
        que se note."""
        from negocio.services import ranking_por_tipo
        p = self._pedido()
        self._item(p, 'playera 1:1', cantidad=2, precio='300')
        filas = ranking_por_tipo()
        self.assertEqual(len(filas), 1)
        self.assertIsNone(filas[0]['tipo'])
        self.assertEqual(filas[0]['piezas'], 2)

    def test_gana_la_keyword_mas_larga_no_la_primera_alfabetica(self):
        """`matches()` a secas devuelve el primer tipo por orden alfabético y
        manda 'new balance' a 'Gorras New Era' por la keyword 'new'. El
        reporte agrupa con la regla correcta."""
        from negocio.services import ranking_por_tipo
        TipoArticulo.objects.create(
            nombre='Gorras New Era', keywords='new era,new', costo=Decimal('150'))
        TipoArticulo.objects.create(
            nombre='New balance', keywords='new balance', costo=Decimal('680'))
        p = self._pedido()
        self._item(p, 'new balance', cantidad=1, precio='1200', costo='150')
        filas = ranking_por_tipo()
        self.assertEqual([f['tipo'] for f in filas], ['New balance'])

    def test_excluye_pedidos_no_pagados(self):
        from negocio.services import ranking_por_tipo
        p = self._pedido(estado=Pedido.PENDIENTE)
        self._item(p, 'gorras', cantidad=5)
        self.assertEqual(ranking_por_tipo(), [])

    def test_filtra_por_rango_de_fechas(self):
        from negocio.services import ranking_por_tipo
        dentro = self._pedido(fecha=datetime.date(2026, 7, 15))
        self._item(dentro, 'gorras', cantidad=2)
        fuera = self._pedido(fecha=datetime.date(2026, 8, 15))
        self._item(fuera, 'tenis', cantidad=9)
        filas = ranking_por_tipo(datetime.date(2026, 7, 1), datetime.date(2026, 8, 1))
        self.assertEqual([f['tipo'] for f in filas], ['Gorras'])
        self.assertEqual(filas[0]['piezas'], 2)

    def test_sin_ventas_devuelve_lista_vacia(self):
        from negocio.services import ranking_por_tipo
        self.assertEqual(ranking_por_tipo(), [])


class MasVendidosViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('mv', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.cli = Cliente.objects.create(nombre='N', telefono='9')
        TipoArticulo.objects.create(nombre='Gorras', keywords='gorra,gorras',
                                    costo=Decimal('240'))
        TipoArticulo.objects.create(nombre='Tenis', keywords='tenis',
                                    costo=Decimal('600'))
        p = Pedido.objects.create(
            cliente=self.cli, costo_producto=Decimal('0'),
            precio_venta=Decimal('1500'), estado=Pedido.PAGADO,
            fecha=datetime.date(2026, 7, 15),
        )
        # Gorras: más piezas, menos ganancia. Tenis: al revés. Así el orden
        # elegido cambia el primer puesto y el test lo puede distinguir.
        PedidoItem.objects.create(
            pedido=p, sku_snapshot='TIENDA-BOT', nombre_snapshot='gorras',
            cantidad=5, costo_unitario=Decimal('240'), precio_unitario=Decimal('260'))
        PedidoItem.objects.create(
            pedido=p, sku_snapshot='TIENDA-BOT', nombre_snapshot='tenis',
            cantidad=1, costo_unitario=Decimal('600'), precio_unitario=Decimal('1400'))

    def test_responde_y_trae_el_ranking(self):
        res = self.client.get('/panel/negocio/mas-vendidos/?mes=2026-07')
        self.assertEqual(res.status_code, 200)
        self.assertEqual([f['tipo'] for f in res.context['filas']], ['Gorras', 'Tenis'])

    def test_ordena_por_ganancia_cuando_se_pide(self):
        res = self.client.get('/panel/negocio/mas-vendidos/?mes=2026-07&orden=ganancia')
        self.assertEqual([f['tipo'] for f in res.context['filas']], ['Tenis', 'Gorras'])

    def test_orden_invalido_cae_al_default_sin_romper(self):
        res = self.client.get('/panel/negocio/mas-vendidos/?mes=2026-07&orden=; DROP TABLE')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['orden'], 'piezas')
        self.assertEqual([f['tipo'] for f in res.context['filas']], ['Gorras', 'Tenis'])

    def test_mes_acota_el_periodo(self):
        res = self.client.get('/panel/negocio/mas-vendidos/?mes=2026-06')
        self.assertEqual(res.context['filas'], [])

    def test_mes_todo_no_filtra(self):
        res = self.client.get('/panel/negocio/mas-vendidos/?mes=todo')
        self.assertEqual(len(res.context['filas']), 2)

    def test_mes_invalido_no_rompe(self):
        res = self.client.get('/panel/negocio/mas-vendidos/?mes=no-es-un-mes')
        self.assertEqual(res.status_code, 200)

    def test_exige_staff(self):
        self.client.logout()
        res = self.client.get('/panel/negocio/mas-vendidos/')
        self.assertEqual(res.status_code, 302)


class TextosSinTipoTests(TestCase):
    """Textos de venta que no matchean ningún TipoArticulo.

    Cuando `matches()` no encuentra tipo, `crear_pedido_tienda_bot` graba
    `costo_unitario = 0` y la venta queda con 100% de margen sin avisar. Esta
    lista es la única forma de ver qué keyword falta antes de que siga pasando.
    """

    def setUp(self):
        self.cli = Cliente.objects.create(nombre='N', telefono='9')
        TipoArticulo.objects.create(nombre='Gorras', keywords='gorra,gorras',
                                    costo=Decimal('240'))

    def _pedido(self, estado=Pedido.PAGADO, descripcion=''):
        return Pedido.objects.create(
            cliente=self.cli, descripcion=descripcion,
            costo_producto=Decimal('0'), precio_venta=Decimal('0'),
            estado=estado, fecha=datetime.date(2026, 7, 15),
        )

    def _item(self, pedido, nombre, cantidad=1, precio='750', costo='0'):
        return PedidoItem.objects.create(
            pedido=pedido, sku_snapshot='TIENDA-BOT', nombre_snapshot=nombre,
            cantidad=cantidad, costo_unitario=Decimal(costo),
            precio_unitario=Decimal(precio),
        )

    def test_lista_el_texto_que_no_matchea(self):
        from negocio.services import textos_sin_tipo
        p = self._pedido()
        self._item(p, 'yezzy', cantidad=4)
        filas = textos_sin_tipo()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['texto'], 'yezzy')
        self.assertEqual(filas[0]['piezas'], 4)
        self.assertEqual(filas[0]['ingreso'], Decimal('3000'))

    def test_ignora_el_texto_que_si_matchea(self):
        from negocio.services import textos_sin_tipo
        p = self._pedido()
        self._item(p, 'gorras', cantidad=10)
        self.assertEqual(textos_sin_tipo(), [])

    def test_agrupa_el_mismo_texto_de_pedidos_distintos(self):
        from negocio.services import textos_sin_tipo
        self._item(self._pedido(), 'Jordan', cantidad=5)
        self._item(self._pedido(), 'Jordan', cantidad=4)
        filas = textos_sin_tipo()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['piezas'], 9)
        self.assertEqual(filas[0]['pedidos'], 2)

    def test_distingue_mayusculas_porque_la_keyword_se_escribe_igual(self):
        """'playera g5' y 'playera G5' son dos textos que el empleado teclea
        distinto; verlos separados ayuda a elegir la keyword."""
        from negocio.services import textos_sin_tipo
        p = self._pedido()
        self._item(p, 'playera g5')
        self._item(p, 'playera G5')
        self.assertEqual(len(textos_sin_tipo()), 2)

    def test_ordena_por_dinero_en_juego(self):
        from negocio.services import textos_sin_tipo
        p = self._pedido()
        self._item(p, 'barato', cantidad=1, precio='100')
        self._item(p, 'caro', cantidad=1, precio='9000')
        self.assertEqual([f['texto'] for f in textos_sin_tipo()], ['caro', 'barato'])

    def test_marca_las_que_se_grabaron_con_costo_cero(self):
        from negocio.services import textos_sin_tipo
        p = self._pedido()
        self._item(p, 'yezzy', costo='0')
        self._item(p, 'otro raro', costo='500')
        filas = {f['texto']: f for f in textos_sin_tipo()}
        self.assertTrue(filas['yezzy']['costo_cero'])
        self.assertFalse(filas['otro raro']['costo_cero'])

    def test_incluye_pedidos_sin_lineas_por_su_descripcion(self):
        from negocio.services import textos_sin_tipo
        p = self._pedido(descripcion='1 playera 1:1')
        p.precio_venta = Decimal('627')
        p.save()
        filas = textos_sin_tipo()
        self.assertEqual([f['texto'] for f in filas], ['1 playera 1:1'])
        self.assertEqual(filas[0]['ingreso'], Decimal('627'))

    def test_excluye_pedidos_no_pagados(self):
        from negocio.services import textos_sin_tipo
        self._item(self._pedido(estado=Pedido.PENDIENTE), 'yezzy')
        self.assertEqual(textos_sin_tipo(), [])

    def test_sin_ventas_huerfanas_devuelve_vacio(self):
        from negocio.services import textos_sin_tipo
        self.assertEqual(textos_sin_tipo(), [])


class TiposListAvisoTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('tl', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.cli = Cliente.objects.create(nombre='N', telefono='9')
        TipoArticulo.objects.create(nombre='Gorras', keywords='gorra,gorras',
                                    costo=Decimal('240'))

    def _venta(self, nombre, cantidad=4, precio='750', costo='0'):
        p = Pedido.objects.create(
            cliente=self.cli, costo_producto=Decimal('0'),
            precio_venta=Decimal(precio) * cantidad, estado=Pedido.PAGADO,
            fecha=datetime.date(2026, 7, 15),
        )
        PedidoItem.objects.create(
            pedido=p, sku_snapshot='TIENDA-BOT', nombre_snapshot=nombre,
            cantidad=cantidad, costo_unitario=Decimal(costo),
            precio_unitario=Decimal(precio))

    def test_muestra_el_aviso_con_los_textos_sueltos(self):
        self._venta('yezzy')
        res = self.client.get('/panel/negocio/tipos/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual([f['texto'] for f in res.context['sin_tipo']], ['yezzy'])
        self.assertContains(res, 'yezzy')

    def test_sin_textos_sueltos_no_hay_aviso(self):
        self._venta('gorras')
        res = self.client.get('/panel/negocio/tipos/')
        self.assertEqual(res.context['sin_tipo'], [])

    def test_expone_el_dinero_en_juego(self):
        self._venta('yezzy', cantidad=4, precio='750')
        res = self.client.get('/panel/negocio/tipos/')
        self.assertEqual(res.context['sin_tipo_ingreso'], Decimal('3000'))
        self.assertEqual(res.context['sin_tipo_piezas'], 4)


class AsignarKeywordTests(TestCase):
    """Asignar desde el aviso el texto suelto a un tipo, como keyword.

    El riesgo real: `matches()` es por substring, así que una keyword corta se
    roba textos de otros tipos. Es exactamente como 'new' (de Gorras New Era)
    se quedó con 'new balance'. Por eso cada asignación se simula antes de
    escribirla.
    """

    def setUp(self):
        self.staff = User.objects.create_user('ak', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.cli = Cliente.objects.create(nombre='N', telefono='9')
        self.yeezy = TipoArticulo.objects.create(
            nombre='Tenis yeezy', keywords='tenis yeezy,yeezy', costo=Decimal('580'))
        self.nb = TipoArticulo.objects.create(
            nombre='New balance', keywords='new balance', costo=Decimal('680'))

    def _venta(self, nombre, cantidad=1, precio='750', costo='0'):
        p = Pedido.objects.create(
            cliente=self.cli, costo_producto=Decimal('0'),
            precio_venta=Decimal(precio) * cantidad, estado=Pedido.PAGADO,
            fecha=datetime.date(2026, 7, 15))
        PedidoItem.objects.create(
            pedido=p, sku_snapshot='TIENDA-BOT', nombre_snapshot=nombre,
            cantidad=cantidad, costo_unitario=Decimal(costo),
            precio_unitario=Decimal(precio))

    def _post(self, **kw):
        datos = {'texto': 'yezzy', 'keyword': 'yezzy', 'tipo_id': self.yeezy.pk}
        datos.update(kw)
        return self.client.post('/panel/negocio/tipos/asignar/', datos, follow=True)

    def test_agrega_la_keyword_al_tipo(self):
        self._venta('yezzy')
        self._post()
        self.yeezy.refresh_from_db()
        self.assertIn('yezzy', self.yeezy.keywords_list)

    def test_el_texto_pasa_a_matchear(self):
        from catalog.services import buscar_tipo_articulo
        self._venta('yezzy')
        self.assertIsNone(buscar_tipo_articulo('yezzy'))
        self._post()
        self.assertEqual(buscar_tipo_articulo('yezzy'), self.yeezy)

    def test_desaparece_del_aviso(self):
        self._venta('yezzy')
        self._post()
        res = self.client.get('/panel/negocio/tipos/')
        self.assertEqual(res.context['sin_tipo'], [])

    def test_rechaza_keyword_que_no_esta_en_el_texto(self):
        """Si la keyword no está contenida en el texto, el texto sigue suelto:
        la asignación no habría servido para nada."""
        self._venta('yezzy')
        res = self._post(keyword='zapatilla')
        self.yeezy.refresh_from_db()
        self.assertNotIn('zapatilla', self.yeezy.keywords_list)
        self.assertContains(res, 'no aparece en')

    def test_rechaza_la_keyword_que_le_roba_un_texto_a_otro_tipo(self):
        """El caso real: 'Gorras New Era' ordena antes que 'New balance', así
        que si se le da la keyword 'new', el matcher del bot (primero
        alfabético) le asigna a 'new balance' el costo de una gorra."""
        gorras = TipoArticulo.objects.create(
            nombre='Gorras New Era', keywords='new era', costo=Decimal('150'))
        self._venta('new balance')
        self._venta('gorra new')
        res = self._post(texto='gorra new', keyword='new', tipo_id=gorras.pk)
        gorras.refresh_from_db()
        self.assertNotIn('new', gorras.keywords_list)
        self.assertContains(res, 'new balance')

    def test_permite_la_keyword_que_no_toca_a_nadie(self):
        self._venta('yezzy')
        self._venta('new balance')
        self._post()
        from catalog.services import buscar_tipo_articulo
        self.assertEqual(buscar_tipo_articulo('new balance'), self.nb)

    def test_rechaza_keyword_vacia(self):
        self._venta('yezzy')
        res = self._post(keyword='   ')
        self.yeezy.refresh_from_db()
        self.assertEqual(self.yeezy.keywords_list, ['tenis yeezy', 'yeezy'])
        self.assertEqual(res.status_code, 200)

    def test_rechaza_tipo_inexistente(self):
        self._venta('yezzy')
        res = self._post(tipo_id=99999)
        self.assertEqual(res.status_code, 404)

    def test_exige_post(self):
        res = self.client.get('/panel/negocio/tipos/asignar/')
        self.assertEqual(res.status_code, 405)

    def test_exige_staff(self):
        self.client.logout()
        res = self.client.post('/panel/negocio/tipos/asignar/', {})
        self.assertEqual(res.status_code, 302)


class VentaSinTipoAvisaTests(TestCase):
    """Cuando ningún tipo matchea, el costo se graba en $0 y la venta queda
    con 100% de margen. El cero es plausible, así que nada lo delata: hay que
    decirlo en voz alta, en el grupo, mientras la persona que sabe sigue ahí.
    """

    def setUp(self):
        TipoArticulo.objects.create(nombre='Gorras', keywords='gorra,gorras',
                                    costo=Decimal('240'))

    def test_el_pedido_expone_los_articulos_sin_tipo(self):
        from negocio.services import crear_pedido_tienda_bot
        pedido = crear_pedido_tienda_bot(items=[
            {'description': 'gorras', 'price': 300, 'qty': 2},
            {'description': 'Jordan', 'price': 750, 'qty': 1},
        ])
        self.assertEqual(pedido.sin_tipo, ['Jordan'])

    def test_sin_huerfanos_la_lista_va_vacia(self):
        from negocio.services import crear_pedido_tienda_bot
        pedido = crear_pedido_tienda_bot(items=[
            {'description': 'gorras', 'price': 300, 'qty': 2}])
        self.assertEqual(pedido.sin_tipo, [])

    def test_no_repite_el_mismo_texto_dos_veces(self):
        from negocio.services import crear_pedido_tienda_bot
        pedido = crear_pedido_tienda_bot(items=[
            {'description': 'Jordan', 'price': 750, 'qty': 1},
            {'description': 'Jordan', 'price': 750, 'qty': 1},
        ])
        self.assertEqual(pedido.sin_tipo, ['Jordan'])

    def test_el_costo_sigue_en_cero_no_se_inventa_uno(self):
        """El aviso no adivina el costo: sigue en 0 hasta que alguien decida."""
        from negocio.services import crear_pedido_tienda_bot
        pedido = crear_pedido_tienda_bot(items=[
            {'description': 'Jordan', 'price': 750, 'qty': 1}])
        self.assertEqual(pedido.costo_producto, Decimal('0'))

    def test_no_avisa_si_algun_tipo_matcheo_aunque_sea_el_equivocado(self):
        """El aviso cubre el costo $0, no el costo MAL asignado — son dos bugs
        distintos. 'new balance' con la keyword 'new' de otro tipo se lleva un
        costo (el de la gorra, $150), así que no queda huérfano y no se avisa.
        Eso es el bug del matcher y se arregla por su lado; mezclarlos acá haría
        que el aviso mintiera sobre lo que garantiza."""
        from negocio.services import crear_pedido_tienda_bot
        TipoArticulo.objects.create(nombre='Gorras New Era',
                                    keywords='new era,new', costo=Decimal('150'))
        pedido = crear_pedido_tienda_bot(items=[
            {'description': 'new balance', 'price': 1200, 'qty': 1}])
        self.assertEqual(pedido.sin_tipo, [])
        self.assertEqual(pedido.costo_producto, Decimal('150'))


@override_settings(NEGOCIO_API_KEY='test-key-123')
class ApiTiendaSinTipoTests(TestCase):
    def setUp(self):
        TipoArticulo.objects.create(nombre='Gorras', keywords='gorra,gorras',
                                    costo=Decimal('240'))

    def _post(self, body, key='test-key-123'):
        return self.client.post(
            '/api/negocio/tienda/', data=json.dumps(body),
            HTTP_AUTHORIZATION=f'Bearer {key}', content_type='application/json')

    def test_devuelve_los_articulos_sin_tipo(self):
        res = self._post({'items': [
            {'description': 'gorras', 'price': 300, 'qty': 1},
            {'description': 'Jordan', 'price': 750, 'qty': 1},
        ]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['sin_tipo'], ['Jordan'])

    def test_lista_vacia_cuando_todo_matchea(self):
        res = self._post({'items': [{'description': 'gorras', 'price': 300, 'qty': 1}]})
        self.assertEqual(res.json()['sin_tipo'], [])


class GananciaWebEnCajaTests(TestCase):
    """De un pedido web solo entra a caja la GANANCIA, y solo cuando está
    liquidado.

    El resto del cobro es dinero que se le debe al proveedor: mientras el
    pedido no esté 100% cubierto no se reconoce nada, ni siquiera la parte
    proporcional del anticipo.

    Y hay un corte: un AjusteCaja (arqueo) declara "el efectivo real es este",
    así que todo lo cobrado hasta esa fecha ya se cuadró contra dinero contado
    a mano. Volver a tocarlo lo restaría dos veces — que es justo el error que
    hundió la caja de $27,258 a $20,553.
    """

    def _order(self, codigo, precio, costo, qty=1):
        from orders.models import Order
        o = Order.objects.create(
            order_code=codigo, customer_name='Ana', customer_phone='5512345678')
        o.items.create(
            product=None, quantity=qty,
            price_snapshot=Decimal(precio),
            cost_snapshot=Decimal(costo) if costo is not None else None,
            sku_snapshot='SKU-1', name_snapshot='Producto')
        return o

    def _pago(self, order, monto, fecha=None):
        from orders.models import OrderPayment
        return OrderPayment.objects.create(
            order=order, fecha=fecha or datetime.date(2026, 8, 20),
            monto=Decimal(monto))

    def _arqueo(self, fecha, monto='0'):
        from negocio.models import AjusteCaja
        return AjusteCaja.objects.create(
            fecha=fecha, monto=Decimal(monto),
            saldo_resultante=Decimal('0'), motivo='arqueo')

    def test_liquidado_aporta_solo_la_ganancia(self):
        from negocio.caja import caja_totales
        o = self._order('W1', '1000', '600')
        self._pago(o, '1000')
        self.assertEqual(caja_totales()['saldo'], Decimal('400'))

    def test_anticipo_no_aporta_nada(self):
        """Mientras falte un peso, todo lo cobrado se le debe al proveedor."""
        from negocio.caja import caja_totales
        o = self._order('W2', '1000', '600')
        self._pago(o, '700')
        self.assertEqual(caja_totales()['saldo'], Decimal('0'))

    def test_al_completarse_recien_aparece_la_ganancia(self):
        from negocio.caja import caja_totales
        o = self._order('W3', '1000', '600')
        self._pago(o, '700')
        self.assertEqual(caja_totales()['saldo'], Decimal('0'))
        self._pago(o, '300')
        self.assertEqual(caja_totales()['saldo'], Decimal('400'))

    def test_pedido_sin_cobrar_no_aporta(self):
        from negocio.caja import caja_totales
        self._order('W4', '1000', '600')
        self.assertEqual(caja_totales()['saldo'], Decimal('0'))

    def test_lo_cobrado_antes_del_arqueo_se_toma_tal_cual(self):
        """El arqueo ya cuadró la caja contra el efectivo real: recalcular esos
        pedidos con la regla nueva los restaría dos veces."""
        from negocio.caja import caja_totales
        viejo = self._order('W5', '1000', '600')
        self._pago(viejo, '1000', fecha=datetime.date(2026, 7, 1))
        self._arqueo(datetime.date(2026, 8, 2))
        self.assertEqual(caja_totales()['saldo'], Decimal('1000'))

    def test_despues_del_arqueo_si_aplica_la_regla(self):
        from negocio.caja import caja_totales
        self._arqueo(datetime.date(2026, 8, 2))
        nuevo = self._order('W6', '1000', '600')
        self._pago(nuevo, '1000', fecha=datetime.date(2026, 8, 25))
        self.assertEqual(caja_totales()['saldo'], Decimal('400'))

    def test_conviven_los_dos_lados_del_arqueo(self):
        from negocio.caja import caja_totales
        viejo = self._order('W7', '1000', '600')
        self._pago(viejo, '1000', fecha=datetime.date(2026, 7, 1))
        self._arqueo(datetime.date(2026, 8, 2))
        nuevo = self._order('W8', '2000', '1500')
        self._pago(nuevo, '2000', fecha=datetime.date(2026, 8, 25))
        self.assertEqual(caja_totales()['saldo'], Decimal('1500'))

    def test_sin_arqueo_la_regla_aplica_a_todo(self):
        from negocio.caja import caja_totales
        o = self._order('W9', '1000', '600')
        self._pago(o, '1000', fecha=datetime.date(2026, 7, 1))
        self.assertEqual(caja_totales()['saldo'], Decimal('400'))

    def test_las_ventas_del_negocio_entran_completas(self):
        """En el negocio la compra al proveedor ya se carga como Gasto: si acá
        también se descontara, saldría dos veces."""
        from negocio.caja import caja_totales
        cli = Cliente.objects.create(nombre='N', telefono='9')
        p = Pedido.objects.create(
            cliente=cli, costo_producto=Decimal('600'), precio_venta=Decimal('1000'),
            estado=Pedido.PAGADO, fecha=datetime.date(2026, 8, 25))
        Pago.objects.create(pedido=p, fecha=p.fecha, monto=Decimal('1000'))
        self.assertEqual(caja_totales()['saldo'], Decimal('1000'))

    def test_ganancia_y_costo_siguen_siendo_consistentes(self):
        o = self._order('W10', '1000', '600', qty=2)
        vendido = sum(i.price_snapshot * i.quantity for i in o.items.all())
        self.assertEqual(o.ganancia, vendido - o.costo_mercancia - o.descuento_aplicado)
