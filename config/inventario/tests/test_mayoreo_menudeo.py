"""Mayoreo / menudeo en el modo Vender: lista de precios por calidad y tipo de venta."""
from decimal import Decimal

from django.urls import reverse

from inventario.models import PrecioLista, precio_lista_de
from inventario.services.lotes import crear_lote
from inventario.services.tipos import crear_variante
from inventario.services.ventas import vender_pares
from inventario.tests.test_vistas_vender import VenderBase
from negocio.models import Pedido


class ListaSembradaTests(VenderBase):
    def test_migracion_siembra_los_cuatro_precios(self):
        precios = {(p.calidad, p.tipo_venta): p.precio for p in PrecioLista.objects.all()}
        self.assertEqual(precios, {
            ('importado', 'mayoreo'): Decimal('750'),
            ('importado', 'menudeo'): Decimal('1200'),
            ('nacional', 'mayoreo'): Decimal('700'),
            ('nacional', 'menudeo'): Decimal('900'),
        })


class PrecioListaDeParTests(VenderBase):
    def par_nacional(self):
        var_n = crear_variante(self.base, 'nacional', costo=Decimal('450'))
        lote = crear_lote(
            via='nacional', origen='manual', usuario=self.user, referencia='N1',
            lineas=[self.linea(tipo=var_n, cantidad=1)])
        return lote.pares.first()

    def test_aereo_y_maritimo_son_importado(self):
        self.assertEqual(precio_lista_de(self.p1), {
            'mayoreo': Decimal('750'), 'menudeo': Decimal('1200')})
        lote_m = crear_lote(
            via='maritimo', origen='manual', usuario=self.user, referencia='M1',
            lineas=[self.linea(tipo=self.var_m, cantidad=1)])
        self.assertEqual(precio_lista_de(lote_m.pares.first())['mayoreo'], Decimal('750'))

    def test_nacional_usa_su_propia_lista(self):
        self.assertEqual(precio_lista_de(self.par_nacional()), {
            'mayoreo': Decimal('700'), 'menudeo': Decimal('900')})

    def test_sin_fila_en_la_lista_no_ofrece_precio(self):
        PrecioLista.objects.filter(calidad='importado', tipo_venta='mayoreo').delete()
        self.assertEqual(precio_lista_de(self.p1), {'menudeo': Decimal('1200')})


class VenderInfoListaTests(VenderBase):
    def test_devuelve_la_lista_segun_la_calidad_del_par(self):
        d = self.post_json('inventario:vender_info', {'lectura': self.p1.codigo}).json()
        self.assertEqual(d['precio_lista'], {'mayoreo': '750.00', 'menudeo': '1200.00'})
        # Los precios por modelo siguen saliendo como botones extra.
        self.assertEqual([x['nombre'] for x in d['precios']], ['Normal', 'Mayoreo'])

    def test_precio_editado_en_la_lista_se_refleja(self):
        PrecioLista.objects.filter(calidad='importado', tipo_venta='menudeo').update(
            precio=Decimal('1300'))
        d = self.post_json('inventario:vender_info', {'lectura': self.p1.codigo}).json()
        self.assertEqual(d['precio_lista']['menudeo'], '1300.00')


class VenderCerrarTipoVentaTests(VenderBase):
    def test_guarda_mayoreo(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '750'}], tipo_venta='mayoreo')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Pedido.objects.get(pk=r.json()['pedido_id']).tipo_venta, 'mayoreo')

    def test_guarda_menudeo(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '1200'}], tipo_venta='menudeo')
        self.assertEqual(Pedido.objects.get(pk=r.json()['pedido_id']).tipo_venta, 'menudeo')

    def test_sin_tipo_o_invalido_400_y_no_vende(self):
        for valor in (None, '', 'medio', 7):
            with self.subTest(valor=valor):
                r = self.post_json('inventario:vender_cerrar', {
                    'lineas': [{'codigo': self.p1.codigo, 'precio': '900'}],
                    'metodo_pago': 'efectivo', 'tipo_venta': valor})
                self.assertEqual(r.status_code, 400)
                self.assertIn('mayoreo', r.json()['error'].lower())
        self.assertEqual(Pedido.objects.count(), 0)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.estado, 'en_stock')

    def test_el_precio_escrito_gana_sobre_la_lista(self):
        r = self.cerrar([{'codigo': self.p1.codigo, 'precio': '800'}], tipo_venta='mayoreo')
        pedido = Pedido.objects.get(pk=r.json()['pedido_id'])
        self.assertEqual(pedido.precio_venta, Decimal('800'))
        self.assertEqual(pedido.ganancia, Decimal('800') - self.p1.costo)


class VenderParesTipoVentaTests(VenderBase):
    def test_servicio_guarda_el_tipo(self):
        pedido = vender_pares([(self.p1, Decimal('750'))], metodo_pago='efectivo',
                              usuario=self.user, tipo_venta='mayoreo')
        self.assertEqual(pedido.tipo_venta, 'mayoreo')

    def test_servicio_rechaza_tipo_invalido(self):
        with self.assertRaises(ValueError):
            vender_pares([(self.p1, Decimal('750'))], metodo_pago='efectivo',
                         usuario=self.user, tipo_venta='medio')
        self.assertEqual(Pedido.objects.count(), 0)


class PantallaVenderTests(VenderBase):
    def test_selector_menudeo_mayoreo_empieza_en_menudeo(self):
        r = self.client.get(reverse('inventario:escanear'))
        self.assertContains(r, 'id="vd-tipo-venta"')
        self.assertContains(r, 'value="menudeo" checked')
        self.assertContains(r, 'value="mayoreo"')


class CatalogoListaTests(VenderBase):
    def test_pestana_precios_muestra_la_lista(self):
        r = self.client.get(reverse('inventario:catalogos'), {'tab': 'precios'})
        self.assertContains(r, 'Lista de precios')
        self.assertContains(r, 'name="precio_lista_id"', count=4)

    def test_editar_un_precio_de_la_lista(self):
        fila = PrecioLista.objects.get(calidad='nacional', tipo_venta='menudeo')
        r = self.client.post(reverse('inventario:catalogos') + '?tab=precios', {
            'accion': 'precio_lista', 'precio_lista_id': fila.pk, 'precio': '950'})
        self.assertEqual(r.status_code, 302)
        fila.refresh_from_db()
        self.assertEqual(fila.precio, Decimal('950'))

    def test_precio_negativo_no_se_guarda(self):
        fila = PrecioLista.objects.get(calidad='nacional', tipo_venta='menudeo')
        self.client.post(reverse('inventario:catalogos') + '?tab=precios', {
            'accion': 'precio_lista', 'precio_lista_id': fila.pk, 'precio': '-5'})
        fila.refresh_from_db()
        self.assertEqual(fila.precio, Decimal('900'))
