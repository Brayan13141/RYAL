from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.forms import ColorForm, ColoresModeloForm
from inventario.models import Color, PrecioVenta, TallaCalzado, TarifaFlete
from negocio.forms import TipoArticuloForm


def _base(nombre='Jordan 4', keywords='jordan 4, j4', **kw):
    t = TipoArticulo(nombre=nombre, keywords=keywords, costo=Decimal('540'), **kw)
    t.full_clean()
    t.save()
    return t


class CatalogosBase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse('inventario:catalogos')


class AccesoTests(CatalogosBase):
    def test_solo_staff(self):
        self.client.logout()
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        u = User.objects.create_user('n', password='x')
        self.client.force_login(u)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)

    def test_staff_ve_la_pantalla_y_el_nav(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, reverse('inventario:tipos_convertir'))


class NavTests(CatalogosBase):
    def test_nav_inventario_en_otra_pantalla_y_descuentos_fuera(self):
        r = self.client.get(reverse('negocio:tipos_list'))
        html = r.content.decode()
        self.assertIn(reverse('inventario:catalogos'), html)
        i, d, c = (html.index('>Inventario<'), html.index('Descuentos</span>'),
                   html.index('>Catálogo<'))
        self.assertLess(d, i)
        self.assertLess(i, c)


class ColorTests(CatalogosBase):
    def test_crear_color_normaliza(self):
        self.client.post(self.url, {'accion': 'color', 'nombre': ' ÁZUL  Rey ', 'alias': 'Celeste, AZUL'})
        c = Color.objects.get()
        self.assertEqual(c.nombre, 'azul rey')
        self.assertEqual(c.alias, 'celeste, azul')

    def test_duplicado_normalizado_es_error_de_form_no_500(self):
        Color.objects.create(nombre='negro')
        f = ColorForm({'nombre': 'NEGRO', 'alias': ''})
        self.assertFalse(f.is_valid())
        self.assertIn('nombre', f.errors)
        r = self.client.post(self.url, {'accion': 'color', 'nombre': 'Négro'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Color.objects.count(), 1)

    def test_editar_color_no_choca_consigo_mismo(self):
        c = Color.objects.create(nombre='negro')
        f = ColorForm({'nombre': 'NEGRO', 'alias': 'oscuro'}, instance=c)
        self.assertTrue(f.is_valid(), f.errors)

    def test_asignar_colores_a_modelo_base(self):
        t = _base()
        a, b = Color.objects.create(nombre='negro'), Color.objects.create(nombre='rojo')
        self.client.post(self.url, {'accion': 'colores_modelo', 'tipo': t.pk, 'colores': [a.pk, b.pk]})
        self.assertEqual(set(t.colores.all()), {a, b})
        self.client.post(self.url, {'accion': 'colores_modelo', 'tipo': t.pk, 'colores': [a.pk]})
        self.assertEqual(set(t.colores.all()), {a})


    def test_guardar_no_desasigna_colores_inactivos_del_modelo(self):
        t = _base()
        a = Color.objects.create(nombre='negro')
        viejo = Color.objects.create(nombre='verde', activo=False)
        nuevo = Color.objects.create(nombre='rojo')
        t.colores.set([a, viejo])
        # El formulario muestra el inactivo marcado; guardar con él debe ser válido.
        form = ColoresModeloForm({'tipo': t.pk, 'colores': [a.pk, viejo.pk, nuevo.pk]})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(set(t.colores.all()), {a, viejo, nuevo})

    def test_inactivo_no_asignado_sigue_sin_poderse_elegir(self):
        t = _base()
        a = Color.objects.create(nombre='negro')
        viejo = Color.objects.create(nombre='verde', activo=False)
        t.colores.set([a])
        self.client.post(self.url, {'accion': 'colores_modelo', 'tipo': t.pk,
                                    'colores': [a.pk, viejo.pk]})
        self.assertEqual(set(t.colores.all()), {a})

    def test_etiqueta_marca_inactivo(self):
        t = _base()
        viejo = Color.objects.create(nombre='verde', activo=False)
        t.colores.set([viejo])
        form = ColoresModeloForm({'tipo': t.pk, 'colores': [viejo.pk]})
        self.assertTrue(form.is_valid(), form.errors)
        etiquetas = [e[1] for e in form.fields['colores'].choices]
        self.assertIn('verde (inactivo)', etiquetas)


class TarifaIdInvalidoTests(CatalogosBase):
    def test_tarifa_id_no_numerico_es_404(self):
        for tid in ('abc', '', '-1'):
            r = self.client.post(self.url, {'accion': 'tarifa', 'tarifa_id': tid, 'monto_por_par': '1'})
            self.assertEqual(r.status_code, 404)


class TallaTests(CatalogosBase):
    def test_crear_talla_sin_eu(self):
        self.client.post(self.url, {'accion': 'talla', 'mx_cm': '27.5', 'orden': 5, 'activa': 'on'})
        t = TallaCalzado.objects.get(mx_cm=Decimal('27.5'))
        self.assertEqual(t.mx_cm, Decimal('27.5'))
        self.assertIsNone(t.eu)


class FleteTests(CatalogosBase):
    def test_editar_tarifa_aerea(self):
        tf = TarifaFlete.objects.get(via='aereo')
        self.client.post(self.url, {'accion': 'tarifa', 'tarifa_id': tf.pk, 'monto_por_par': '300'})
        tf.refresh_from_db()
        self.assertEqual(tf.monto_por_par, Decimal('300'))


class PrecioVentaTests(CatalogosBase):
    def test_crear_en_base(self):
        t = _base()
        self.client.post(self.url, {'accion': 'precio', 'tipo': t.pk, 'nombre': 'Menudeo',
                                    'precio': '900', 'orden': 0})
        p = PrecioVenta.objects.get()
        self.assertEqual((p.nombre, p.precio), ('Menudeo', Decimal('900')))

    def test_rechaza_variante(self):
        t = _base()
        v = TipoArticulo(nombre='Jordan 4 A', keywords='jordan 4 aereo', costo=Decimal('540'),
                         padre=t, via='aereo', usa_tallas=True)
        v.full_clean()
        v.save()
        r = self.client.post(self.url, {'accion': 'precio', 'tipo': v.pk, 'nombre': 'Menudeo',
                                        'precio': '900', 'orden': 0})
        self.assertEqual(PrecioVenta.objects.count(), 0)
        self.assertContains(r, 'va en el modelo base')


class TipoArticuloFormTests(TestCase):
    def test_acepta_prefijo(self):
        f = TipoArticuloForm({'nombre': 'Jordan 4', 'keywords': 'jordan 4', 'costo': '540',
                              'prefijo': 'JD4', 'usa_tallas': 'on'})
        self.assertTrue(f.is_valid(), f.errors)
        t = f.save()
        self.assertEqual(t.prefijo, 'JD4')
        self.assertTrue(t.usa_tallas)

    def test_error_de_keyword_de_via(self):
        base = _base()
        f = TipoArticuloForm({'nombre': 'Jordan 4 A', 'keywords': 'jordan 4 a', 'costo': '540',
                              'padre': base.pk, 'via': 'aereo', 'usa_tallas': 'on'})
        self.assertFalse(f.is_valid())
        self.assertIn('keywords', f.errors)

    def test_padre_solo_modelos_base(self):
        base = _base()
        v = TipoArticulo(nombre='J4A', keywords='j4 aereo', costo=Decimal('1'), padre=base, via='aereo',
                         usa_tallas=True)
        v.full_clean()
        v.save()
        qs = TipoArticuloForm().fields['padre'].queryset
        self.assertIn(base, qs)
        self.assertNotIn(v, qs)


class ColoresDeVarianteVanAlBaseTests(TestCase):
    """Los colores viven en el modelo base (colores_efectivos, el alta, el bot y
    el import leen solo de ahí). Marcar un color al editar la variante aérea
    lo guardaba en la variante y el alta nunca lo mostraba."""

    def setUp(self):
        self.base = _base()
        self.negro = Color.objects.create(nombre='negro')
        self.rojo = Color.objects.create(nombre='rojo')
        self.base.colores.add(self.negro)
        self.var = TipoArticulo(nombre='Jordan 4 A', keywords='jordan 4 aereo', costo=Decimal('620'),
                                padre=self.base, via='aereo', usa_tallas=True, prefijo='JD4A')
        self.var.full_clean()
        self.var.save()

    def _datos(self, colores):
        return {'nombre': 'Jordan 4 A', 'keywords': 'jordan 4 aereo', 'costo': '620',
                'padre': self.base.pk, 'via': 'aereo', 'usa_tallas': 'on', 'prefijo': 'JD4A',
                'colores': [c.pk for c in colores]}

    def test_editar_variante_muestra_los_colores_del_base(self):
        f = TipoArticuloForm(instance=self.var)
        self.assertEqual(list(f.initial['colores']), [self.negro])

    def test_color_marcado_en_la_variante_queda_en_el_base(self):
        f = TipoArticuloForm(self._datos([self.negro, self.rojo]), instance=self.var)
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        self.assertEqual(set(self.base.colores.all()), {self.negro, self.rojo})
        self.assertFalse(self.var.colores.exists())
        self.assertIn(self.rojo, self.var.colores_efectivos())

    def test_nueva_variante_suma_colores_sin_quitar_los_del_base(self):
        self.var.delete()
        f = TipoArticuloForm(self._datos([self.rojo]))
        self.assertTrue(f.is_valid(), f.errors)
        nueva = f.save()
        self.assertEqual(set(self.base.colores.all()), {self.negro, self.rojo})
        self.assertFalse(nueva.colores.exists())

    def test_el_alta_ofrece_el_color_agregado_desde_la_variante(self):
        f = TipoArticuloForm(self._datos([self.negro, self.rojo]), instance=self.var)
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        staff = User.objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(staff)
        r = self.client.get(reverse('inventario:alta_manual'))
        modelo = next(m for m in r.context['modelos'] if m['id'] == self.base.pk)
        self.assertIn('rojo', [c['nombre'] for c in modelo['colores']])


class MigracionColoresDeVarianteTests(TestCase):
    def test_mueve_los_colores_de_la_variante_al_base(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module('catalog.migrations.0040_colores_de_variante_al_base')
        base = _base()
        negro, rojo = Color.objects.create(nombre='negro'), Color.objects.create(nombre='rojo')
        base.colores.add(negro)
        var = TipoArticulo(nombre='Jordan 4 A', keywords='jordan 4 aereo', costo=Decimal('620'),
                           padre=base, via='aereo', usa_tallas=True)
        var.full_clean()
        var.save()
        var.colores.add(rojo)
        mig.mover_colores_al_base(apps, None)
        self.assertEqual(set(base.colores.all()), {negro, rojo})
        self.assertFalse(var.colores.exists())
