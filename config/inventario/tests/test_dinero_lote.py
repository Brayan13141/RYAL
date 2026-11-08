"""Dinero del lote: lo cobrado por cada par vendido (lo que debería haber en caja) contra el
costo del lote (lo que falta por recuperar)."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from inventario.models import Par, TallaCalzado
from inventario.services import ventas
from inventario.services.dinero_lote import dinero_del_lote
from inventario.services.lotes import crear_lote
from inventario.tests.test_vistas_lotes import LotesVistaBase


class DineroLoteBase(LotesVistaBase):
    def setUp(self):
        super().setUp()
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.lote = crear_lote(
            via='aereo', origen='manual', usuario=self.staff, referencia='R1',
            lineas=[{'tipo': self.var_a, 'color': self.color, 'talla': self.t26,
                     'cantidad': 4, 'precio_proveedor': Decimal('360')}])
        self.p1, self.p2, self.p3, self.p4 = self.lote.pares.order_by('pk')
        for p in (self.p1, self.p2, self.p3):
            p.transicionar('en_stock', usuario=self.staff, origen='escaner')
        # p1 en stock, p2 vendido en 900, p3 vendido en 850 (después), p4 baja por daño.
        self.venta_p2 = ventas.vender_pares(
            [(self.p2, Decimal('900'))], metodo_pago='efectivo', usuario=self.staff)
        self.venta_p3 = ventas.vender_pares(
            [(self.p3, Decimal('850'))], metodo_pago='efectivo', usuario=self.staff)
        Par.objects.filter(pk=self.p3.pk).update(vendido_at=timezone.now() + timedelta(minutes=5))
        self.p4.transicionar('baja', usuario=self.staff, origen='cierre_lote', motivo='faltante')
        self.costo_total = sum((p.costo for p in self.lote.pares.all()), Decimal('0'))


class DineroDelLoteTests(DineroLoteBase):
    def test_totales(self):
        d = dinero_del_lote(self.lote)
        self.assertEqual(d['cobrado'], Decimal('1750'))
        self.assertEqual(d['costo'], self.costo_total)
        self.assertEqual(d['falta'], self.costo_total - Decimal('1750'))
        self.assertEqual(d['vendidos'], 2)
        self.assertEqual(d['sin_precio'], 0)

    def test_lista_por_par_mas_reciente_primero(self):
        d = dinero_del_lote(self.lote)
        self.assertEqual(len(d['grupos']), 1)
        g = d['grupos'][0]
        self.assertEqual(g['modelo'], 'Jordan 4')
        self.assertEqual(g['via'], 'Aéreo')
        self.assertEqual([f['par'].codigo for f in g['pares']], [self.p3.codigo, self.p2.codigo])
        self.assertEqual([f['precio'] for f in g['pares']], [Decimal('850'), Decimal('900')])
        self.assertEqual(g['pares'][1]['pedido_id'], self.venta_p2.pk)
        self.assertEqual((g['n'], g['cobrado']), (2, Decimal('1750')))
        self.assertEqual(g['costo'], self.p2.costo + self.p3.costo)

    def test_cancelar_la_venta_la_quita(self):
        ventas.cancelar_pedido(self.venta_p2, usuario=self.staff, nota='se arrepintió')
        d = dinero_del_lote(self.lote)
        self.assertEqual(d['cobrado'], Decimal('850'))
        self.assertEqual(d['vendidos'], 1)
        self.assertEqual(d['costo'], self.costo_total)  # el costo del lote no cambia

    def test_par_vendido_sin_precio_cuenta_cero_y_se_avisa(self):
        self.p1.transicionar('vendido', usuario=self.staff, origen='escaner')  # sin ítem
        d = dinero_del_lote(self.lote)
        self.assertEqual(d['sin_precio'], 1)
        self.assertEqual(d['cobrado'], Decimal('1750'))
        fila = next(f for f in d['grupos'][0]['pares'] if f['par'].pk == self.p1.pk)
        self.assertIsNone(fila['precio'])

    def test_anulados_no_cuentan_en_el_costo(self):
        Par.objects.filter(pk=self.p1.pk).update(estado=Par.Estado.ANULADO)
        d = dinero_del_lote(self.lote)
        self.assertEqual(d['costo'], self.costo_total - self.p1.costo)

    def test_lote_sin_ventas(self):
        lote = crear_lote(
            via='aereo', origen='manual', usuario=self.staff, referencia='R2',
            lineas=[{'tipo': self.var_a, 'color': self.color, 'talla': self.t26,
                     'cantidad': 1, 'precio_proveedor': Decimal('360')}])
        d = dinero_del_lote(lote)
        self.assertEqual((d['cobrado'], d['vendidos'], d['grupos']), (Decimal('0'), 0, []))


class DineroLoteVistaTests(DineroLoteBase):
    def detalle(self):
        return self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))

    def test_muestra_seccion_totales_y_pares(self):
        r = self.detalle()
        self.assertEqual(r.context['dinero']['cobrado'], Decimal('1750'))
        self.assertContains(r, 'Dinero del lote')
        self.assertContains(r, 'Falta por recuperar')
        self.assertContains(r, self.p2.codigo)
        self.assertContains(r, reverse('negocio:pedido_detail', args=[self.venta_p2.pk]))

    def test_ganancia_cuando_lo_cobrado_supera_el_costo(self):
        ventas.vender_pares([(self.p1, Decimal('99999'))], metodo_pago='efectivo',
                            usuario=self.staff)
        r = self.detalle()
        self.assertLess(r.context['dinero']['falta'], 0)
        self.assertContains(r, 'Ganancia')
        self.assertNotContains(r, 'Falta por recuperar')

    def test_aviso_sin_precio(self):
        self.p1.transicionar('vendido', usuario=self.staff, origen='escaner')
        self.assertContains(self.detalle(), '1 par vendido sin precio')

    def test_lote_sin_ventas_no_muestra_lista(self):
        ventas.cancelar_pedido(self.venta_p2, usuario=self.staff, nota='x')
        ventas.cancelar_pedido(self.venta_p3, usuario=self.staff, nota='x')
        r = self.detalle()
        self.assertContains(r, 'Dinero del lote')
        self.assertContains(r, 'Aún no se vende ningún par de este lote.')
