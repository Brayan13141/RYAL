"""Un par vendido sí llegó: cuenta como recibido en la lista de lotes, el encabezado del
lote, las cards por modelo y los renglones, y se muestra aparte cuántos se vendieron."""
from decimal import Decimal

from django.urls import reverse

from inventario.models import TallaCalzado
from inventario.services.lotes import cerrar_lote, crear_lote
from inventario.tests.test_vistas_lotes import LotesVistaBase


class LoteVendidosTests(LotesVistaBase):
    def setUp(self):
        super().setUp()
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.lote = crear_lote(
            via='aereo', origen='manual', usuario=self.staff, referencia='R1',
            lineas=[{'tipo': self.var_a, 'color': self.color, 'talla': self.t26,
                     'cantidad': 4, 'precio_proveedor': Decimal('360')}])
        p1, p2, p3, _p4 = self.lote.pares.order_by('pk')
        for p in (p1, p2, p3):
            p.transicionar('en_stock', usuario=self.staff, origen='escaner')
        p2.transicionar('vendido', usuario=self.staff, origen='escaner')
        p3.transicionar('baja', usuario=self.staff, origen='manual', nota='roto', motivo='danado')
        # p1 en stock, p2 vendido, p3 baja por daño (sí llegó), p4 sin escanear.

    def detalle(self):
        return self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))

    def test_encabezado_cuenta_vendidos_y_bajas_como_recibidos(self):
        r = self.detalle()
        self.assertEqual(r.context['recibidos'], 3)
        self.assertContains(r, '<span class="inv-stat-n">3</span> <span class="inv-muted">de 4 recibidos</span>', html=False)
        self.assertContains(r, '75% del lote')
        self.assertContains(r, '1 vendido')

    def test_card_y_renglon(self):
        r = self.detalle()
        m = r.context['modelos'][0]
        self.assertEqual((m['esperados'], m['recibidos'], m['vendidos'], m['pendientes']),
                         (4, 3, 1, 1))
        f = r.context['filas'][0]
        self.assertEqual((f['recibidos'], f['vendidos']), (3, 1))
        self.assertContains(r, '3 de 4 recibidos · 1 vendido')

    def test_faltante_no_cuenta_como_recibido(self):
        cerrar_lote(self.lote, usuario=self.staff)
        r = self.detalle()
        self.assertEqual(r.context['recibidos'], 3)
        self.assertEqual(r.context['modelos'][0]['recibidos'], 3)

    def test_lista_de_lotes(self):
        r = self.client.get(reverse('inventario:lotes'))
        lote = next(l for l in r.context['lotes'] if l.pk == self.lote.pk)
        self.assertEqual((lote.total, lote.recibidos, lote.vendidos), (4, 3, 1))
        self.assertContains(r, '1 vendido')

    def test_lote_sin_ventas_no_muestra_vendidos(self):
        p2 = self.lote.pares.get(estado='vendido')
        p2.transicionar('en_stock', usuario=self.staff, origen='deshacer', nota='x')
        r = self.detalle()
        self.assertNotContains(r, 'vendido ·')
        self.assertNotContains(r, '· 0 vendidos')
