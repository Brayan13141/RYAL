"""Dinero del lote: lo cobrado por cada par vendido (lo que debería haber en caja) contra el
costo del lote (lo que falta por recuperar), y la pantalla aparte de pares vendidos."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from inventario.models import Color, Par, TallaCalzado
from inventario.services import ventas
from inventario.services.dinero_lote import dinero_del_lote, vendidos_del_lote
from inventario.services.lotes import crear_lote
from inventario.services.tipos import crear_variante
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
        # p1 en stock, p2 vendido en 900, p3 vendido en 850 (después), p4 baja.
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

    def test_cancelar_la_venta_la_quita(self):
        ventas.cancelar_pedido(self.venta_p2, usuario=self.staff, nota='se arrepintió')
        d = dinero_del_lote(self.lote)
        self.assertEqual((d['cobrado'], d['vendidos']), (Decimal('850'), 1))
        self.assertEqual(d['costo'], self.costo_total)  # el costo del lote no cambia

    def test_par_vendido_sin_precio_cuenta_cero_y_se_avisa(self):
        self.p1.transicionar('vendido', usuario=self.staff, origen='escaner')  # sin ítem
        d = dinero_del_lote(self.lote)
        self.assertEqual((d['sin_precio'], d['cobrado']), (1, Decimal('1750')))

    def test_anulados_no_cuentan_en_el_costo(self):
        Par.objects.filter(pk=self.p1.pk).update(estado=Par.Estado.ANULADO)
        self.assertEqual(dinero_del_lote(self.lote)['costo'], self.costo_total - self.p1.costo)

    def test_lote_sin_ventas(self):
        lote = crear_lote(
            via='aereo', origen='manual', usuario=self.staff, referencia='R2',
            lineas=[{'tipo': self.var_a, 'color': self.color, 'talla': self.t26,
                     'cantidad': 1, 'precio_proveedor': Decimal('360')}])
        d = dinero_del_lote(lote)
        self.assertEqual((d['cobrado'], d['vendidos']), (Decimal('0'), 0))


class VendidosDelLoteTests(DineroLoteBase):
    def setUp(self):
        super().setUp()
        # Un par más de otro modelo, color y talla, vendido ayer en 1200.
        self.rojo = Color.objects.create(nombre='rojo')
        self.base.colores.add(self.rojo)  # los colores viven en el modelo base
        self.base_b = crear_variante(self.base, 'maritimo', costo=Decimal('500'))
        self.t27 = TallaCalzado.objects.get(mx_cm=Decimal('27.5'))
        self.lote_b = crear_lote(
            via='maritimo', origen='manual', usuario=self.staff, referencia='R3',
            lineas=[{'tipo': self.base_b, 'color': self.rojo, 'talla': self.t27,
                     'cantidad': 1, 'precio_proveedor': Decimal('300')}])
        # Se mueve al lote del setUp para tener dos modelos en un mismo lote.
        self.p5 = self.lote_b.pares.get()
        Par.objects.filter(pk=self.p5.pk).update(lote=self.lote)
        self.p5.refresh_from_db()
        self.p5.transicionar('en_stock', usuario=self.staff, origen='escaner')
        self.venta_p5 = ventas.vender_pares(
            [(self.p5, Decimal('1200'))], metodo_pago='efectivo', usuario=self.staff)
        Par.objects.filter(pk=self.p5.pk).update(vendido_at=timezone.now() - timedelta(days=1))

    def test_sin_filtros_mas_reciente_primero(self):
        v = vendidos_del_lote(self.lote)
        self.assertEqual([f['par'].codigo for f in v['filas']],
                         [self.p3.codigo, self.p2.codigo, self.p5.codigo])
        self.assertEqual(v['filas'][1]['precio'], Decimal('900'))
        self.assertEqual(v['filas'][1]['pedido_id'], self.venta_p2.pk)
        self.assertEqual((v['filas'][0]['modelo'], v['filas'][0]['via']), ('Jordan 4', 'Aéreo'))
        self.assertEqual(v['filas'][2]['talla'], '27.5')
        self.assertEqual((v['n'], v['cobrado']), (3, Decimal('2950')))
        self.assertEqual(v['costo'], self.p2.costo + self.p3.costo + self.p5.costo)
        self.assertEqual(v['margen'], v['cobrado'] - v['costo'])

    def test_opciones_solo_de_lo_vendido(self):
        o = vendidos_del_lote(self.lote)['opciones']
        self.assertEqual([m[0] for m in o['modelos']], [self.var_a.pk, self.base_b.pk])
        self.assertEqual([c[1] for c in o['colores']], ['negro', 'rojo'])
        self.assertEqual([t[1] for t in o['tallas']], ['26', '27.5'])

    def test_filtra_por_modelo_color_y_talla(self):
        self.assertEqual(vendidos_del_lote(self.lote, tipo_id=self.base_b.pk)['n'], 1)
        self.assertEqual(vendidos_del_lote(self.lote, color_id=self.color.pk)['n'], 2)
        v = vendidos_del_lote(self.lote, talla_id=self.t27.pk)
        self.assertEqual((v['n'], v['cobrado']), (1, Decimal('1200')))

    def test_filtra_por_fechas(self):
        hoy = timezone.localdate()
        self.assertEqual(vendidos_del_lote(self.lote, desde=hoy)['n'], 2)
        self.assertEqual(vendidos_del_lote(self.lote, hasta=hoy - timedelta(days=1))['n'], 1)

    def test_dinero_del_lote_usa_los_mismos_vendidos(self):
        self.assertEqual(dinero_del_lote(self.lote)['cobrado'], Decimal('2950'))


class LoteDetalleDineroVistaTests(DineroLoteBase):
    def detalle(self):
        return self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))

    def test_totales_y_boton_a_la_pantalla_aparte(self):
        r = self.detalle()
        self.assertEqual(r.context['dinero']['cobrado'], Decimal('1750'))
        self.assertContains(r, 'Dinero del lote')
        self.assertContains(r, 'Falta por recuperar')
        self.assertContains(r, reverse('inventario:lote_vendidos', args=[self.lote.pk]))
        self.assertContains(r, 'Ver pares vendidos (2)')
        self.assertNotContains(r, self.p2.codigo + '</a>')  # la lista ya no vive aquí

    def test_ganancia_cuando_lo_cobrado_supera_el_costo(self):
        ventas.vender_pares([(self.p1, Decimal('99999'))], metodo_pago='efectivo',
                            usuario=self.staff)
        r = self.detalle()
        self.assertContains(r, 'Ganancia')
        self.assertNotContains(r, 'Falta por recuperar')

    def test_aviso_sin_precio(self):
        self.p1.transicionar('vendido', usuario=self.staff, origen='escaner')
        self.assertContains(self.detalle(), '1 par vendido sin precio')

    def test_sin_ventas_no_hay_boton(self):
        ventas.cancelar_pedido(self.venta_p2, usuario=self.staff, nota='x')
        ventas.cancelar_pedido(self.venta_p3, usuario=self.staff, nota='x')
        r = self.detalle()
        self.assertContains(r, 'Aún no se vende ningún par de este lote.')
        self.assertNotContains(r, reverse('inventario:lote_vendidos', args=[self.lote.pk]))


class LoteVendidosVistaTests(DineroLoteBase):
    def url(self, **q):
        base = reverse('inventario:lote_vendidos', args=[self.lote.pk])
        return base + ('?' + '&'.join(f'{k}={v}' for k, v in q.items()) if q else '')

    def test_lista_totales_y_enlaces(self):
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.p2.codigo)
        self.assertContains(r, self.p3.codigo)
        self.assertContains(r, reverse('negocio:pedido_detail', args=[self.venta_p2.pk]))
        self.assertContains(r, reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertEqual(r.context['vendidos']['n'], 2)

    def test_filtro_por_talla_inexistente_no_lista_nada(self):
        otra = TallaCalzado.objects.exclude(pk=self.t26.pk).first()
        r = self.client.get(self.url(talla=otra.pk))
        self.assertEqual(r.context['vendidos']['n'], 0)
        self.assertContains(r, 'Ningún par vendido con esos filtros.')

    def test_valores_invalidos_se_ignoran(self):
        r = self.client.get(self.url(modelo='abc', color='', talla='x', desde='31-31-2026',
                                     hasta='ayer'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['vendidos']['n'], 2)

    def test_conserva_los_filtros_en_el_formulario(self):
        hoy = timezone.localdate().isoformat()
        r = self.client.get(self.url(desde=hoy, color=self.color.pk))
        self.assertContains(r, f'value="{hoy}"')
        self.assertContains(r, f'<option value="{self.color.pk}" selected>')

    def test_solo_staff(self):
        self.client.logout()
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 302)

    def test_lote_inexistente_404(self):
        r = self.client.get(reverse('inventario:lote_vendidos', args=[999999]))
        self.assertEqual(r.status_code, 404)
