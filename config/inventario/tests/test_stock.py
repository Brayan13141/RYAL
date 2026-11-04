from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import TipoArticulo
from inventario.models import Lote, MovimientoPar, Par
from inventario.services.stock import alertas, matriz_stock
from inventario.tests.test_venta_bot import VentaBotBase
from negocio.models import Pedido, PedidoItem

E = Par.Estado


class StockBase(VentaBotBase):
    def setUp(self):
        super().setUp()
        self.lote_m = Lote.objects.create(
            referencia='LM', origen='manual', via='maritimo', flete_por_par=Decimal('180'))

    def par_m(self, estado=E.EN_STOCK, color=None, talla=None):
        self._n += 1
        return Par.objects.create(
            codigo=f'JD4M-{self._n:04d}', tipo=self.maritimo, color=color or self.negro,
            talla=talla or self.t27, lote=self.lote_m, estado=estado, costo=Decimal('500'))

    def item_de(self, pedido):
        return pedido.items.get()


class MatrizTests(StockBase):
    def test_cuenta_solo_en_stock_y_separa_aereo_maritimo(self):
        self.par()
        self.par()
        self.par_m()
        self.par(estado=E.VENDIDO)
        self.par(estado=E.POR_RECIBIR)
        self.par(estado=E.BAJA)
        m = matriz_stock()
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]['base'], self.base)
        self.assertEqual(m[0]['total'], 3)
        fila = m[0]['colores'][0]
        self.assertEqual(fila['color'], self.negro)
        self.assertEqual(fila['total'], 3)
        self.assertEqual(fila['tallas'][Decimal('27.0')], {'aereo': 2, 'maritimo': 1, 'nacional': 0})

    def test_colores_totales_y_filtros(self):
        self.par(talla=self.t27)
        self.par(talla=self.t265, color=self.marino)
        self.par_m()
        m = matriz_stock()
        colores = {f['color'].nombre: f for f in m[0]['colores']}
        self.assertEqual(set(colores), {'negro', 'marino'})
        self.assertEqual(m[0]['total'], 3)
        solo_m = matriz_stock(via='maritimo')
        self.assertEqual(solo_m[0]['total'], 1)
        self.assertEqual(matriz_stock(base_id=self.base.pk + 999), [])
        self.assertEqual(matriz_stock(base_id=self.base.pk)[0]['total'], 3)


    def test_filtro_estado(self):
        self.par()
        self.par(estado=E.VENDIDO)
        self.par(estado=E.VENDIDO)
        self.par(estado=E.POR_RECIBIR)
        self.par(estado=E.BAJA)
        self.assertEqual(matriz_stock()[0]['total'], 1)  # por defecto en_stock
        self.assertEqual(matriz_stock(estado='en_stock')[0]['total'], 1)
        self.assertEqual(matriz_stock(estado='vendido')[0]['total'], 2)
        self.assertEqual(matriz_stock(estado='por_recibir')[0]['total'], 1)
        self.assertEqual(matriz_stock(estado='baja')[0]['total'], 1)


class AlertasTests(StockBase):
    def test_venta_del_bot_sin_par_aparece_con_faltantes(self):
        self.par()
        pedido = self.vender('jordan 4 aereo negro t27', qty=3)  # solo 1 par
        a = alertas()['ventas_bot_sin_par']
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]['item'].pedido_id, pedido.pk)
        self.assertEqual(a[0]['faltan'], 2)

    def test_venta_completa_no_aparece(self):
        self.par()
        self.vender('jordan 4 aereo negro t27', qty=1)
        self.assertEqual(alertas()['ventas_bot_sin_par'], [])

    def test_cancelada_no_aparece(self):
        pedido = self.vender('jordan 4 aereo negro t27', qty=1)
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.CANCELADO)
        self.assertEqual(alertas()['ventas_bot_sin_par'], [])

    def test_historico_resuelto_a_base_o_otro_tipo_no_aparece(self):
        TipoArticulo.objects.create(nombre='Gorra', keywords='gorra', costo=Decimal('50'))
        pedido = self.vender('jordan 4 aereo negro t27', qty=1)
        PedidoItem.objects.filter(pk=self.item_de(pedido).pk).update(nombre_snapshot='jordan 4')
        PedidoItem.objects.create(
            pedido=pedido, sku_snapshot='TIENDA-BOT', nombre_snapshot='gorra negra',
            cantidad=2, costo_unitario=Decimal('0'), precio_unitario=Decimal('1'))
        self.assertEqual(alertas()['ventas_bot_sin_par'], [])

    def test_lotes_abiertos_con_faltantes_umbral_de_7_dias(self):
        def lote(ref, **kw):
            return Lote.objects.create(
                referencia=ref, origen='manual', via='aereo',
                flete_por_par=Decimal('280'), **kw)
        viejo, nuevo, sin_pend = lote('viejo'), lote('nuevo'), lote('sinpend')
        cerrado = lote('cerrado', cerrado_at=timezone.now())
        ahora = timezone.now()
        Lote.objects.filter(pk=viejo.pk).update(creado_at=ahora - timedelta(days=8))
        Lote.objects.filter(pk=nuevo.pk).update(creado_at=ahora - timedelta(days=6))
        Lote.objects.filter(pk=cerrado.pk).update(creado_at=ahora - timedelta(days=9))
        Lote.objects.filter(pk=sin_pend.pk).update(creado_at=ahora - timedelta(days=9))
        for i, l in enumerate((viejo, nuevo, cerrado)):
            Par.objects.create(
                codigo=f'JD4A-9{i:03d}', tipo=self.aereo, color=self.negro, talla=self.t27,
                lote=l, estado=E.POR_RECIBIR, costo=Decimal('1'))
        Par.objects.create(
            codigo='JD4A-9100', tipo=self.aereo, color=self.negro, talla=self.t27,
            lote=sin_pend, estado=E.EN_STOCK, costo=Decimal('1'))
        self.assertEqual(alertas()['lotes_abiertos_con_faltantes'], [viejo])

    def test_vendidos_bot_sin_escaneo_cuenta_solo_los_del_bot(self):
        a = self.par(estado=E.VENDIDO)
        b = self.par(estado=E.VENDIDO)
        self.par(estado=E.VENDIDO)  # sin movimientos
        mov = MovimientoPar.objects.create
        mov(par=a, de_estado='en_stock', a_estado='vendido', origen='bot')
        mov(par=b, de_estado='en_stock', a_estado='vendido', origen='bot')
        mov(par=b, de_estado='vendido', a_estado='en_stock', origen='deshacer')
        mov(par=b, de_estado='en_stock', a_estado='vendido', origen='escaner')
        self.assertEqual(alertas()['vendidos_bot_sin_escaneo'], 1)


class VistaStockTests(StockBase):
    def test_solo_staff(self):
        url = reverse('inventario:stock')
        self.assertEqual(url, '/panel/inventario/')
        self.assertEqual(self.client.get(url).status_code, 302)
        gente = get_user_model().objects.create_user('x', password='x')
        self.client.force_login(gente)
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_staff_ve_matriz_y_alertas_y_filtros_invalidos_se_ignoran(self):
        staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(staff)
        self.par()
        self.vender('jordan 4 aereo negro t26.5', qty=2)
        r = self.client.get(reverse('inventario:stock'), {'base_id': 'abc', 'via': 'zzz'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Jordan 4')
        self.assertContains(r, 'Ventas del bot sin par')
        r = self.client.get(reverse('inventario:stock'), {'via': 'maritimo'})
        self.assertEqual(r.status_code, 200)

    def test_filtro_estado_en_la_vista_y_valor_invalido_se_ignora(self):
        staff = get_user_model().objects.create_user('st2', password='x', is_staff=True)
        self.client.force_login(staff)
        self.par()
        self.par(estado=E.VENDIDO)
        self.par(estado=E.VENDIDO)
        r = self.client.get(reverse('inventario:stock'), {'estado': 'vendido'})
        self.assertEqual(r.context['estado'], 'vendido')
        self.assertEqual(r.context['tablas'][0]['total'], 2)
        r = self.client.get(reverse('inventario:stock'), {'estado': 'zzz'})
        self.assertEqual(r.context['estado'], 'en_stock')
        self.assertEqual(r.context['tablas'][0]['total'], 1)
        r = self.client.get(reverse('inventario:stock'))
        self.assertEqual(r.context['estado'], 'en_stock')
