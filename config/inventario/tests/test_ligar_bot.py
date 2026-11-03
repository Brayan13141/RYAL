"""Task 18b: 'Ya se vendió por el bot' (candidatos, endpoint y orden de candados)."""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from inventario.models import Par
from inventario.services.ventas import cancelar_pedido, items_bot_pendientes, ligar_a_item
from inventario.tests.test_venta_bot import VentaBotBase
from negocio.models import Pago, Pedido, PedidoItem

E = Par.Estado


class LigarBotBase(VentaBotBase):
    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)

    def post_json(self, nombre, datos, **kw):
        return self.client.post(
            reverse(nombre, **kw), json.dumps(datos), content_type='application/json')

    def item_de(self, pedido):
        return pedido.items.get()


class ItemsBotPendientesTests(LigarBotBase):
    def test_aparece_la_misma_combinacion_y_no_otro_color(self):
        self.par(color=self.negro, dias=1)  # solo 1 en stock: la venta de 2 queda con 1 libre
        pedido_negro = self.vender('jordan 4 aereo negro t27', qty=2)
        pedido_marino = self.vender('jordan 4 aereo marino t27', qty=1)
        escaneado = self.par(color=self.negro)
        lista = items_bot_pendientes(escaneado)
        ids = [i.pk for i, _ in lista]
        self.assertIn(self.item_de(pedido_negro).pk, ids)
        self.assertNotIn(self.item_de(pedido_marino).pk, ids)
        libres = {i.pk: l for i, l in lista}
        self.assertEqual(libres[self.item_de(pedido_negro).pk], 1)

    def test_no_aparece_otra_talla(self):
        self.vender('jordan 4 aereo negro t26.5', qty=1)
        escaneado = self.par(color=self.negro, talla=self.t27)
        self.assertEqual(items_bot_pendientes(escaneado), [])

    def test_pedido_cancelado_no_aparece(self):
        self.par()
        pedido = self.vender('jordan 4 aereo negro t27', qty=2)
        cancelar_pedido(pedido, usuario=self.staff, nota='x')
        escaneado = self.par()
        self.assertEqual(items_bot_pendientes(escaneado), [])

    def test_item_lleno_con_par_del_bot_aparece_con_cero_libres(self):
        self.par()
        pedido = self.vender('jordan 4 aereo negro t27', qty=1)
        escaneado = self.par()
        lista = items_bot_pendientes(escaneado)
        self.assertEqual([(i.pk, l) for i, l in lista], [(self.item_de(pedido).pk, 0)])

    def test_item_lleno_por_escaneo_no_aparece(self):
        # Venta del bot sin stock (queda libre) que se completó escaneando: lleno sin par del bot.
        pedido = self.vender('jordan 4 aereo negro t27', qty=1)
        item = self.item_de(pedido)
        asignado = self.par()
        ligar_a_item(asignado, item, usuario=self.staff)
        self.assertEqual(item.pares.count(), 1)
        self.assertFalse(any(
            p.movimientos.filter(origen='bot').exists() for p in item.pares.all()))
        self.assertEqual(items_bot_pendientes(self.par()), [])

    def test_pendiente_viejo_no_lo_desplazan_los_ya_conciliados(self):
        from unittest import mock
        pendiente = self.item_de(self.vender('jordan 4 aereo negro t27', qty=1))
        for _ in range(3):  # conciliados por escaneo, más recientes que el pendiente
            it = self.item_de(self.vender('jordan 4 aereo negro t27', qty=1))
            ligar_a_item(self.par(), it, usuario=self.staff)
        escaneado = self.par()
        with mock.patch('inventario.services.ventas._MAX_ESCANEO_BOT', 2):
            ids = [i.pk for i, _ in items_bot_pendientes(escaneado)]
        self.assertEqual(ids, [pendiente.pk])

    def test_mas_recientes_primero(self):
        self.par(dias=3)
        self.par(dias=2)
        p1 = self.vender('jordan 4 aereo negro t27', qty=1)
        p2 = self.vender('jordan 4 aereo negro t27', qty=1)
        escaneado = self.par()
        ids = [i.pk for i, _ in items_bot_pendientes(escaneado)]
        self.assertEqual(ids, [self.item_de(p2).pk, self.item_de(p1).pk])

    def test_cargar_tipos_una_sola_vez(self):
        from unittest import mock
        self.vender('jordan 4 aereo negro t27', qty=1)
        self.vender('jordan 4 aereo negro t27', qty=1)
        escaneado = self.par()
        with mock.patch('inventario.services.ventas.cargar_aliases',
                        wraps=__import__('negocio.services', fromlist=['x']).cargar_aliases) as m:
            items_bot_pendientes(escaneado)
        self.assertEqual(m.call_count, 1)


class LigarCanceladoTests(LigarBotBase):
    def test_ligar_en_venta_cancelada_es_rojo_y_no_cambia_nada(self):
        self.par()
        pedido = self.vender('jordan 4 aereo negro t27', qty=2)
        item = self.item_de(pedido)
        cancelar_pedido(pedido, usuario=self.staff, nota='x')
        escaneado = self.par()
        pagos, pedidos = Pago.objects.count(), Pedido.objects.count()
        r = ligar_a_item(escaneado, item, usuario=self.staff)
        self.assertEqual(r.nivel, 'rojo')
        escaneado.refresh_from_db()
        self.assertEqual(escaneado.estado, E.EN_STOCK)
        self.assertIsNone(escaneado.pedido_item_id)
        self.assertEqual(item.pares.count(), 0)
        self.assertEqual((Pago.objects.count(), Pedido.objects.count()), (pagos, pedidos))

    def test_usa_la_cantidad_fresca_del_item(self):
        # El objeto `item` que llega puede estar viejo: manda lo leído bajo candado.
        self.par()
        pedido = self.vender('jordan 4 aereo negro t27', qty=1)
        item_viejo = self.item_de(pedido)
        PedidoItem.objects.filter(pk=item_viejo.pk).update(cantidad=2)
        escaneado = self.par()
        r = ligar_a_item(escaneado, item_viejo, usuario=self.staff)
        self.assertEqual(r.nivel, 'verde')
        self.assertEqual(item_viejo.pares.count(), 2)


class VenderInfoBotTests(LigarBotBase):
    def test_en_stock_trae_items_bot(self):
        self.par()
        pedido = self.vender('jordan 4 aereo negro t27', qty=1)
        escaneado = self.par()
        d = self.post_json('inventario:vender_info', {'lectura': escaneado.codigo}).json()
        self.assertEqual(d['nivel'], 'verde')
        self.assertEqual(len(d['items_bot']), 1)
        fila = d['items_bot'][0]
        self.assertEqual(fila['id'], self.item_de(pedido).pk)
        self.assertEqual(fila['pedido_id'], pedido.pk)
        self.assertEqual(fila['nombre_snapshot'], 'jordan 4 aereo negro t27')
        self.assertEqual(fila['libres'], 0)
        self.assertIn('fecha', fila)

    def test_en_stock_sin_ventas_del_bot_lista_vacia(self):
        d = self.post_json('inventario:vender_info', {'lectura': self.par().codigo}).json()
        self.assertEqual(d['items_bot'], [])

    def test_vendido_por_el_bot_con_hermano_en_stock_es_amarillo(self):
        vendido = self.par(dias=2)
        pedido = self.vender('jordan 4 aereo negro t27', qty=1)
        vendido.refresh_from_db()
        self.assertEqual(vendido.estado, E.VENDIDO)
        self.par()  # hermano en stock
        d = self.post_json('inventario:vender_info', {'lectura': vendido.codigo}).json()
        self.assertEqual(d['nivel'], 'amarillo')
        self.assertIn('bot', d['mensaje'].lower())
        self.assertIn('intercambi', d['mensaje'].lower())
        self.assertEqual([f['id'] for f in d['items_bot']], [self.item_de(pedido).pk])
        self.assertNotIn('precios', d)

    def test_vendido_por_el_bot_sin_hermano_es_rojo(self):
        vendido = self.par()
        self.vender('jordan 4 aereo negro t27', qty=1)
        d = self.post_json('inventario:vender_info', {'lectura': vendido.codigo}).json()
        self.assertEqual(d['nivel'], 'rojo')

    def test_vendido_por_escaner_con_hermano_sigue_rojo(self):
        vendido = self.par()
        vendido.transicionar('vendido', usuario=self.staff, origen='escaner')
        self.par()
        d = self.post_json('inventario:vender_info', {'lectura': vendido.codigo}).json()
        self.assertEqual(d['nivel'], 'rojo')


class VenderLigarEndpointTests(LigarBotBase):
    def test_liga_a_lugar_libre_sin_crear_pedido_ni_pago(self):
        self.par()
        pedido = self.vender('jordan 4 aereo negro t27', qty=2)
        item = self.item_de(pedido)
        escaneado = self.par()
        pedidos, pagos = Pedido.objects.count(), Pago.objects.count()
        costo = item.costo_unitario
        r = self.post_json('inventario:vender_ligar',
                           {'codigo': escaneado.codigo, 'item_id': item.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['nivel'], 'verde')
        escaneado.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(escaneado.estado, E.VENDIDO)
        self.assertEqual(escaneado.pedido_item_id, item.pk)
        self.assertEqual(item.costo_unitario, costo)
        self.assertEqual((Pedido.objects.count(), Pago.objects.count()), (pedidos, pagos))

    def test_intercambio_por_el_endpoint(self):
        del_bot = self.par(dias=2)
        pedido = self.vender('jordan 4 aereo negro t27', qty=1)
        item = self.item_de(pedido)
        escaneado = self.par()
        r = self.post_json('inventario:vender_ligar',
                           {'codigo': escaneado.codigo, 'item_id': item.pk})
        self.assertEqual(r.json()['nivel'], 'verde')
        del_bot.refresh_from_db()
        escaneado.refresh_from_db()
        self.assertEqual(del_bot.estado, E.EN_STOCK)
        self.assertEqual(escaneado.estado, E.VENDIDO)
        self.assertEqual(escaneado.pedido_item_id, item.pk)

    def test_venta_cancelada_es_400_por_el_endpoint(self):
        self.par()
        pedido = self.vender('jordan 4 aereo negro t27', qty=2)
        item = self.item_de(pedido)
        cancelar_pedido(pedido, usuario=self.staff, nota='x')
        escaneado = self.par()
        r = self.post_json('inventario:vender_ligar',
                           {'codigo': escaneado.codigo, 'item_id': item.pk})
        self.assertEqual(r.status_code, 400)
        escaneado.refresh_from_db()
        self.assertEqual(escaneado.estado, E.EN_STOCK)

    def test_item_de_otra_variante_se_rechaza_400(self):
        self.par()
        pedido = self.vender('jordan 4 aereo marino t27', qty=1)
        item = self.item_de(pedido)
        escaneado = self.par(color=self.negro)
        r = self.post_json('inventario:vender_ligar',
                           {'codigo': escaneado.codigo, 'item_id': item.pk})
        self.assertEqual(r.status_code, 400)
        escaneado.refresh_from_db()
        self.assertEqual(escaneado.estado, E.EN_STOCK)

    def test_item_que_no_es_del_bot_se_rechaza(self):
        escaneado = self.par()
        otro = Pedido.objects.create(
            costo_producto=Decimal('1'), precio_venta=Decimal('2'), origen=Pedido.TIENDA)
        item = PedidoItem.objects.create(
            pedido=otro, sku_snapshot='X', nombre_snapshot='x', cantidad=1,
            costo_unitario=Decimal('1'), precio_unitario=Decimal('2'))
        r = self.post_json('inventario:vender_ligar',
                           {'codigo': escaneado.codigo, 'item_id': item.pk})
        self.assertEqual(r.status_code, 400)
        escaneado.refresh_from_db()
        self.assertEqual(escaneado.estado, E.EN_STOCK)

    def test_datos_invalidos(self):
        r = self.post_json('inventario:vender_ligar', {'codigo': 'NOEXISTE-1', 'item_id': 1})
        self.assertEqual(r.json()['nivel'], 'rojo')
        r = self.post_json('inventario:vender_ligar', {'codigo': 'X', 'item_id': 'abc'})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(reverse('inventario:vender_ligar'), 'x',
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_requiere_staff(self):
        self.client.logout()
        r = self.post_json('inventario:vender_ligar', {'codigo': 'X', 'item_id': 1})
        self.assertEqual(r.status_code, 302)


class EditarItemConParesTests(LigarBotBase):
    """M6: editar a mano un ítem con pares ligados no puede desincronizarlos."""

    def setUp(self):
        super().setUp()
        self.par()
        self.par()
        self.pedido = self.vender('jordan 4 aereo negro t27', qty=2)
        self.item = self.item_de(self.pedido)
        self.assertEqual(self.item.pares.count(), 2)

    def datos(self, **kw):
        d = {'nombre_snapshot': self.item.nombre_snapshot, 'cantidad': self.item.cantidad,
             'costo_unitario': self.item.costo_unitario,
             'precio_unitario': self.item.precio_unitario}
        d.update(kw)
        return d

    def editar(self, **kw):
        return self.client.post(
            reverse('negocio:pedido_item_edit', args=[self.item.pk]), self.datos(**kw))

    def test_bajar_cantidad_por_debajo_de_los_pares_se_rechaza(self):
        r = self.editar(cantidad=1)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Quitar par en el pedido')
        self.item.refresh_from_db()
        self.assertEqual(self.item.cantidad, 2)

    def test_cambiar_el_nombre_se_rechaza(self):
        r = self.editar(nombre_snapshot='otra cosa')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Quitar par en el pedido')
        self.item.refresh_from_db()
        self.assertNotEqual(self.item.nombre_snapshot, 'otra cosa')

    def test_subir_cantidad_o_cambiar_precio_se_permite(self):
        r = self.editar(cantidad=3, precio_unitario='999')
        self.assertEqual(r.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.cantidad, 3)
        self.assertEqual(self.item.precio_unitario, Decimal('999'))

    def test_item_sin_pares_se_edita_como_siempre(self):
        libre = PedidoItem.objects.create(
            pedido=self.pedido, sku_snapshot='X', nombre_snapshot='gorra',
            cantidad=3, costo_unitario=Decimal('1'), precio_unitario=Decimal('2'))
        r = self.client.post(
            reverse('negocio:pedido_item_edit', args=[libre.pk]),
            {'nombre_snapshot': 'gorra azul', 'cantidad': 1,
             'costo_unitario': '1', 'precio_unitario': '2'})
        self.assertEqual(r.status_code, 302)
        libre.refresh_from_db()
        self.assertEqual((libre.nombre_snapshot, libre.cantidad), ('gorra azul', 1))
