"""Interfaz del inventario: navegación común y conteos por estado en stock."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import Color, Conteo, Par, TallaCalzado
from inventario.services.lotes import crear_lote
from inventario.services.tipos import crear_variante


class InterfazBase(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            'st', password='x', is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)
        base = TipoArticulo(nombre='Jordan 4', keywords='jordan 4', costo=Decimal('540'))
        base.full_clean()
        base.save()
        color = Color.objects.create(nombre='negro')
        base.colores.add(color)
        self.var_a = crear_variante(base, 'aereo', costo=Decimal('640'))
        self.lote = crear_lote(
            via='aereo', origen='manual', usuario=self.staff,
            lineas=[{'tipo': self.var_a, 'color': color,
                     'talla': TallaCalzado.objects.get(mx_cm=Decimal('26')),
                     'cantidad': 3, 'precio_proveedor': Decimal('360')}])
        Par.objects.filter(pk=self.lote.pares.first().pk).update(estado='en_stock')


class NavegacionTests(InterfazBase):
    def paginas(self):
        par = self.lote.pares.first()
        conteo = Conteo.objects.create(creado_por=self.staff)
        return [
            reverse('inventario:stock'), reverse('inventario:lotes'),
            reverse('inventario:lote_detalle', args=[self.lote.pk]),
            reverse('inventario:alta_manual'), reverse('inventario:importar'),
            reverse('inventario:escanear'), reverse('inventario:conteos'),
            reverse('inventario:conteo_resumen', args=[conteo.pk]),
            reverse('inventario:catalogos'), reverse('inventario:tipos_convertir'),
            reverse('inventario:par_ficha', args=[par.codigo]),
        ]

    def test_todas_las_pantallas_traen_la_navegacion_y_la_hoja_de_estilos(self):
        for url in self.paginas():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertContains(r, 'class="inv-nav"', msg_prefix=url)
            self.assertContains(r, 'css/inventario.css', msg_prefix=url)

    def test_la_seccion_actual_queda_marcada(self):
        r = self.client.get(reverse('inventario:lotes'))
        self.assertContains(r, f'href="{reverse("inventario:lotes")}" class="inv-nav-link is-active"')


class FichaEtiquetaTests(InterfazBase):
    def test_la_ficha_muestra_la_etiqueta_con_su_qr_real(self):
        from inventario.etiquetas import qr_svg, url_par
        par = self.lote.pares.first()
        r = self.client.get(reverse('inventario:par_ficha', args=[par.codigo]))
        self.assertContains(r, 'class="inv-label"')
        self.assertContains(r, '<svg')
        # El mismo QR que imprime la etiqueta: misma URL, mismo dibujo.
        from django.conf import settings
        esperado = qr_svg(url_par(settings.INVENTARIO_QR_BASE_URL, par.codigo))
        self.assertIn(esperado, r.content.decode())
        self.assertContains(r, 'EU 41')

    def test_qr_svg_es_svg_sin_declaracion_xml(self):
        from inventario.etiquetas import qr_svg
        svg = qr_svg('https://x.test/panel/inventario/p/AB-1234/')
        self.assertTrue(svg.startswith('<svg'), svg[:40])
        self.assertNotIn('<?xml', svg)


class StockPorEstadoTests(InterfazBase):
    def test_pestanas_con_el_conteo_de_cada_estado(self):
        r = self.client.get(reverse('inventario:stock'))
        conteos = dict((c['valor'], c['n']) for c in r.context['pestanas'])
        self.assertEqual(conteos['por_recibir'], 2)
        self.assertEqual(conteos['en_stock'], 1)
        self.assertEqual(conteos['vendido'], 0)
        self.assertContains(r, f'href="?estado=por_recibir"')

    def test_las_pestanas_conservan_los_filtros(self):
        r = self.client.get(reverse('inventario:stock'), {'via': 'aereo'})
        self.assertContains(r, 'href="?estado=por_recibir&amp;via=aereo"')

    def test_el_conteo_respeta_el_filtro_de_via(self):
        r = self.client.get(reverse('inventario:stock'), {'via': 'maritimo'})
        conteos = dict((c['valor'], c['n']) for c in r.context['pestanas'])
        self.assertEqual(conteos['por_recibir'], 0)
