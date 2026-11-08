from decimal import Decimal

from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import PrecioVenta
from inventario.services import cantidad as svc
from inventario.tests.test_vistas_vender import VenderBase
from negocio.models import Pago, Pedido


class VenderCantidadTests(VenderBase):
    def setUp(self):
        super().setUp()
        self.gorra = TipoArticulo(
            nombre='Gorra Barbas', keywords='gorra barbas', costo=Decimal('240'))
        self.gorra.full_clean()
        self.gorra.save()
        PrecioVenta.objects.create(
            tipo=self.gorra, nombre='Normal', precio=Decimal('250'), orden=1)
        svc.activar(self.gorra, 999, usuario=self.staff)
        self.etiqueta = svc.codigo_etiqueta(self.gorra)

    def test_info_reconoce_etiqueta(self):
        url = f'https://x.test/panel/inventario/p/{self.etiqueta}/'
        for lectura in (self.etiqueta, url):
            with self.subTest(lectura=lectura):
                d = self.post_json('inventario:vender_info', {'lectura': lectura}).json()
                self.assertEqual(d['nivel'], 'verde')
                self.assertIs(d['cantidad'], True)
                self.assertNotIn('costo', d)
                self.assertNotIn('costo', d['par'])
                self.assertEqual(d['par']['nombre'], 'Gorra Barbas')
                self.assertEqual(d['precio_lista'], {})
                self.assertEqual(d['precios'], [{'nombre': 'Normal', 'precio': '250.00'}])

    def test_info_tipo_no_activo_es_rojo(self):
        otro = TipoArticulo(nombre='Gorra B', keywords='gorra b', costo=Decimal('1'))
        otro.full_clean()
        otro.save()
        d = self.post_json(
            'inventario:vender_info', {'lectura': svc.codigo_etiqueta(otro)}).json()
        self.assertEqual(d['nivel'], 'rojo')

    def test_cerrar_dos_gorras_y_un_par(self):
        r = self.cerrar([{'codigo': self.etiqueta, 'precio': '250'},
                         {'codigo': self.etiqueta, 'precio': '250'},
                         {'codigo': self.p1.codigo, 'precio': '900'}])
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['ok'])
        self.assertEqual(svc.stock_actual(self.gorra), 997)
        pedido = Pedido.objects.get(pk=r.json()['pedido_id'])
        gorras = pedido.items.filter(sku_snapshot=self.etiqueta)
        self.assertEqual(gorras.count(), 2)
        for it in gorras:
            self.assertEqual(it.costo_unitario, Decimal('240'))
        self.assertTrue(pedido.items.filter(sku_snapshot=self.p1.codigo).exists())
        self.assertEqual(Pago.objects.get(pedido=pedido).monto, Decimal('1400'))
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'vendido')

    def test_solo_gorras(self):
        r = self.cerrar([{'codigo': self.etiqueta, 'precio': '250'}])
        self.assertTrue(r.json()['ok'])

    def test_con_stock_cero_vende(self):
        svc.ajustar(self.gorra, 0, motivo='prueba', usuario=self.staff)
        r = self.cerrar([{'codigo': self.etiqueta, 'precio': '250'}])
        self.assertTrue(r.json()['ok'])
        self.assertEqual(svc.stock_actual(self.gorra), -1)

    def test_par_repetido_sigue_rechazado(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '900'},
                         {'codigo': self.p1.codigo, 'precio': '900'}])
        self.assertEqual(r.status_code, 400)

    def test_precio_invalido_de_gorra(self):
        r = self.cerrar([{'codigo': self.etiqueta, 'precio': ''}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(svc.stock_actual(self.gorra), 999)

    def test_pide_confirmacion_si_el_bot_vendio_hace_poco(self):
        from negocio.services import crear_pedido_tienda_bot
        crear_pedido_tienda_bot(
            items=[{'description': 'gorra barbas', 'price': 250, 'qty': 1}])
        lineas = [{'codigo': self.etiqueta, 'precio': '250'}]
        r = self.cerrar(lineas)
        self.assertEqual(r.status_code, 409)
        d = r.json()
        self.assertTrue(d['requiere_confirmacion'])
        self.assertIn('¿Es una venta distinta?', d['error'])
        r = self.post_json('inventario:vender_cerrar', {
            'lineas': lineas, 'metodo_pago': 'efectivo', 'tipo_venta': 'menudeo',
            'confirmar_venta_nueva': True})
        self.assertTrue(r.json()['ok'])

    def test_cancelar_desde_escaner_devuelve(self):
        r = self.cerrar([{'codigo': self.etiqueta, 'precio': '250'}])
        self.assertEqual(svc.stock_actual(self.gorra), 998)
        self.client.post(reverse('inventario:venta_cancelar', args=[r.json()['pedido_id']]),
                         {'nota': 'Cliente se arrepintió'})
        self.assertEqual(svc.stock_actual(self.gorra), 999)

    def test_salida_del_escaner_guarda_usuario(self):
        from inventario.models import MovimientoCantidad
        r = self.cerrar([{'codigo': self.etiqueta, 'precio': '250'}])
        self.assertTrue(r.json()['ok'])
        mov = MovimientoCantidad.objects.get(pedido_item__pedido_id=r.json()['pedido_id'])
        self.assertEqual(mov.usuario, self.staff)
