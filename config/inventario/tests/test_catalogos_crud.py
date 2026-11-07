"""CRUD completo de Catálogos: editar, borrar y cambiar estado de colores, tallas,
fletes, lista de precios y precios extra por modelo."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import (
    Color, Lote, Par, PrecioLista, PrecioVenta, TallaCalzado, TarifaFlete)


def _base(nombre='Jordan 4', keywords='jordan 4, j4'):
    t = TipoArticulo(nombre=nombre, keywords=keywords, costo=Decimal('540'))
    t.full_clean()
    t.save()
    return t


class CrudBase(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('st', password='x', is_staff=True))
        self.url = reverse('inventario:catalogos')

    def post(self, **datos):
        return self.client.post(self.url, datos, follow=True)

    def par(self, color, talla):
        tipo = _base(nombre=f'Modelo {Par.objects.count()}', keywords=f'kw{Par.objects.count()}')
        lote = Lote.objects.create(referencia='L', origen='manual', via='aereo',
                                   flete_por_par=Decimal('0'))
        return Par.objects.create(codigo=f'X{Par.objects.count()}', tipo=tipo, color=color,
                                  talla=talla, lote=lote, costo=Decimal('100'))


class ColorCrudTests(CrudBase):
    def test_editar_color(self):
        c = Color.objects.create(nombre='azul 1', alias='celeste')
        self.post(accion='color', pk=c.pk, nombre='Azul', alias='celeste, marino', activo='on')
        c.refresh_from_db()
        self.assertEqual((c.nombre, c.alias, c.activo), ('azul', 'celeste, marino', True))
        self.assertEqual(Color.objects.count(), 1)

    def test_editar_a_un_nombre_existente_es_error(self):
        Color.objects.create(nombre='negro')
        c = Color.objects.create(nombre='rojo')
        r = self.post(accion='color', pk=c.pk, nombre='NEGRO', activo='on')
        c.refresh_from_db()
        self.assertEqual(c.nombre, 'rojo')
        self.assertContains(r, 'Ya existe el color')

    def test_get_editar_precarga_el_formulario(self):
        c = Color.objects.create(nombre='verde olivo')
        r = self.client.get(self.url, {'tab': 'colores', 'editar': c.pk})
        self.assertContains(r, 'Editar color')
        self.assertContains(r, 'value="verde olivo"')
        self.assertContains(r, f'name="pk" value="{c.pk}"')

    def test_editar_inexistente_es_404(self):
        r = self.client.get(self.url, {'tab': 'colores', 'editar': 999})
        self.assertEqual(r.status_code, 404)
        r = self.client.post(self.url, {'accion': 'color', 'pk': 'abc', 'nombre': 'x'})
        self.assertEqual(r.status_code, 404)

    def test_cambiar_estado(self):
        c = Color.objects.create(nombre='rojo')
        self.post(accion='color_estado', pk=c.pk)
        c.refresh_from_db()
        self.assertFalse(c.activo)
        self.post(accion='color_estado', pk=c.pk)
        c.refresh_from_db()
        self.assertTrue(c.activo)

    def test_borrar_color_libre_y_lo_quita_de_los_modelos(self):
        t = _base()
        c = Color.objects.create(nombre='rojo')
        t.colores.set([c])
        r = self.post(accion='color_borrar', pk=c.pk)
        self.assertFalse(Color.objects.exists())
        self.assertContains(r, 'Color borrado')

    def test_borrar_color_en_uso_se_bloquea(self):
        c = Color.objects.create(nombre='negro')
        self.par(c, TallaCalzado.objects.first())
        r = self.post(accion='color_borrar', pk=c.pk)
        self.assertTrue(Color.objects.filter(pk=c.pk).exists())
        self.assertContains(r, 'lo usan 1 par')
        self.assertContains(r, 'Desactív')

    def test_tabla_tiene_botones_de_editar_estado_y_borrar(self):
        c = Color.objects.create(nombre='rojo')
        r = self.client.get(self.url, {'tab': 'colores'})
        self.assertContains(r, f'?tab=colores&amp;editar={c.pk}')
        self.assertContains(r, 'value="color_estado"')
        self.assertContains(r, 'value="color_borrar"')

    def test_editar_colores_de_un_modelo_precarga_los_asignados(self):
        t = _base()
        a, b = Color.objects.create(nombre='negro'), Color.objects.create(nombre='rojo')
        t.colores.set([a])
        r = self.client.get(self.url, {'tab': 'colores', 'modelo': t.pk})
        form = r.context['forms']['colores_modelo']
        self.assertEqual(form.initial['tipo'], t.pk)
        self.assertEqual(list(form.initial['colores']), [a.pk])


class TallaCrudTests(CrudBase):
    def test_editar_talla(self):
        t, _ = TallaCalzado.objects.get_or_create(mx_cm=Decimal('25.5'), defaults={'orden': 50})
        self.post(accion='talla', pk=t.pk, mx_cm='25.5', eu='40', us_hombre='8',
                  us_mujer='9.5', cn_mm='255', orden='50', activa='on')
        t.refresh_from_db()
        self.assertEqual((t.eu, t.cn_mm), (Decimal('40'), 255))

    def test_cambiar_estado(self):
        t = TallaCalzado.objects.create(mx_cm=Decimal('31'), orden=99)
        self.post(accion='talla_estado', pk=t.pk)
        t.refresh_from_db()
        self.assertFalse(t.activa)

    def test_borrar_talla_libre(self):
        t = TallaCalzado.objects.create(mx_cm=Decimal('31'), orden=99)
        self.post(accion='talla_borrar', pk=t.pk)
        self.assertFalse(TallaCalzado.objects.filter(pk=t.pk).exists())

    def test_borrar_talla_en_uso_se_bloquea(self):
        t = TallaCalzado.objects.create(mx_cm=Decimal('31'), orden=99)
        self.par(Color.objects.create(nombre='negro'), t)
        r = self.post(accion='talla_borrar', pk=t.pk)
        self.assertTrue(TallaCalzado.objects.filter(pk=t.pk).exists())
        self.assertContains(r, 'lo usan 1 par')


class FleteCrudTests(CrudBase):
    def test_borrar_y_volver_a_dar_de_alta(self):
        tf = TarifaFlete.objects.get(via='nacional')
        self.post(accion='tarifa_borrar', pk=tf.pk)
        self.assertFalse(TarifaFlete.objects.filter(via='nacional').exists())
        r = self.client.get(self.url, {'tab': 'fletes'})
        self.assertContains(r, 'value="tarifa_nueva"')
        self.post(accion='tarifa_nueva', via='nacional', monto_por_par='60')
        self.assertEqual(TarifaFlete.objects.get(via='nacional').monto_por_par, Decimal('60'))

    def test_alta_de_via_repetida_es_error(self):
        r = self.post(accion='tarifa_nueva', via='aereo', monto_por_par='1')
        self.assertEqual(TarifaFlete.objects.filter(via='aereo').count(), 1)
        self.assertEqual(r.status_code, 200)

    def test_sin_vias_libres_no_se_ofrece_alta(self):
        r = self.client.get(self.url, {'tab': 'fletes'})
        self.assertNotContains(r, 'value="tarifa_nueva"')

    def test_monto_negativo_es_error(self):
        r = self.post(accion='tarifa_nueva', via='nacional', monto_por_par='-5')
        self.assertEqual(r.status_code, 200)


class PrecioListaCrudTests(CrudBase):
    def test_borrar_y_dar_de_alta_combinacion(self):
        PrecioLista.objects.all().delete()
        self.post(accion='precio_lista_nueva', calidad='nacional', tipo_venta='mayoreo',
                  precio='350')
        p = PrecioLista.objects.get()
        self.assertEqual(p.precio, Decimal('350'))
        self.post(accion='precio_lista_borrar', pk=p.pk)
        self.assertFalse(PrecioLista.objects.exists())

    def test_combinacion_repetida_es_error(self):
        PrecioLista.objects.all().delete()
        PrecioLista.objects.create(calidad='nacional', tipo_venta='mayoreo', precio=1)
        self.post(accion='precio_lista_nueva', calidad='nacional', tipo_venta='mayoreo',
                  precio='2')
        self.assertEqual(PrecioLista.objects.count(), 1)


class PrecioVentaCrudTests(CrudBase):
    def test_editar_y_borrar(self):
        t = _base()
        p = PrecioVenta.objects.create(tipo=t, nombre='Menudeo', precio=900)
        self.post(accion='precio', pk=p.pk, tipo=t.pk, nombre='Menudeo', precio='950', orden='0')
        p.refresh_from_db()
        self.assertEqual(p.precio, Decimal('950'))
        self.assertEqual(PrecioVenta.objects.count(), 1)
        self.post(accion='precio_borrar', pk=p.pk)
        self.assertFalse(PrecioVenta.objects.exists())

    def test_get_editar_precarga(self):
        t = _base()
        p = PrecioVenta.objects.create(tipo=t, nombre='Promo', precio=799)
        r = self.client.get(self.url, {'tab': 'precios', 'editar': p.pk})
        self.assertContains(r, 'Editar precio')
        self.assertContains(r, 'value="Promo"')


class EstiloInputsTests(CrudBase):
    def test_inputs_traen_clases_del_panel(self):
        r = self.client.get(self.url, {'tab': 'tallas'})
        html = r.content.decode()
        self.assertIn('name="mx_cm"', html)
        self.assertRegex(html, r'<input(?=[^>]*name="mx_cm")[^>]*class="form-control"')
        self.assertIn('form-switch', html)
        r = self.client.get(self.url, {'tab': 'precios'})
        self.assertRegex(r.content.decode(), r'<select(?=[^>]*name="tipo")[^>]*class="form-select"')

    def test_borrar_pide_confirmacion(self):
        Color.objects.create(nombre='rojo')
        r = self.client.get(self.url, {'tab': 'colores'})
        self.assertContains(r, 'return confirm(')

    def test_accion_desconocida_no_truena(self):
        r = self.post(accion='nada_borrar', pk=1)
        self.assertContains(r, 'Acción desconocida')
