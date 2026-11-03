import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.etiquetas import pdf_etiquetas
from inventario.models import Color, Par, TallaCalzado
from inventario.services.lotes import crear_lote
from inventario.services.tipos import crear_variante


def _paginas(pdf):
    return pdf.count(b'/Type /Page') - pdf.count(b'/Type /Pages')


class EtiquetasBase(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.base = TipoArticulo(nombre='Jordan 4', keywords='jordan 4', costo=Decimal('540'))
        self.base.full_clean()
        self.base.save()
        self.color = Color.objects.create(nombre='negro')
        self.base.colores.add(self.color)
        self.var_a = crear_variante(self.base, 'aereo', costo=Decimal('640'))
        self.lote = crear_lote(
            via='aereo', origen='manual', usuario=self.staff,
            lineas=[
                {'tipo': self.var_a, 'color': self.color,
                 'talla': TallaCalzado.objects.get(mx_cm=Decimal('26')),
                 'cantidad': 2, 'precio_proveedor': Decimal('360')},
                {'tipo': self.var_a, 'color': self.color,
                 'talla': TallaCalzado.objects.get(mx_cm=Decimal('25.5')),
                 'cantidad': 1, 'precio_proveedor': Decimal('360')},
            ])


class PdfEtiquetasTests(EtiquetasBase):
    def test_una_pagina_por_par_y_tamano_54x44(self):
        pdf = pdf_etiquetas(self.lote.pares.all(), base_url='https://x.test')
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(_paginas(pdf), 3)
        cajas = re.findall(rb'/MediaBox\s*\[\s*([\d.\s]+?)\s*\]', pdf)
        self.assertEqual(len(cajas), 3)
        for caja in cajas:
            x0, y0, w, h = (float(n) for n in caja.split())
            self.assertEqual((x0, y0), (0.0, 0.0))
            self.assertAlmostEqual(w, 153.07, delta=0.01)
            self.assertAlmostEqual(h, 124.72, delta=0.01)

    def test_talla_sin_eu_no_rompe_y_omite_el_dato(self):
        par = self.lote.pares.get(talla__mx_cm=Decimal('25.5'))
        pdf = pdf_etiquetas([par], base_url='https://x.test')
        self.assertEqual(_paginas(pdf), 1)
        # Solo los textos dibujados (el PDF trae el QR como binario).
        textos = re.findall(rb'\((.*?)\) Tj', pdf)
        self.assertIn(b'25.5', textos)
        self.assertFalse([t for t in textos if b'EU' in t or b'None' in t], textos)

    def test_nombre_largo_no_rompe(self):
        self.base.nombre = 'Modelo con un nombre larguisimo que no cabe jamas en la etiqueta'
        self.base.save()
        pdf = pdf_etiquetas(self.lote.pares.all()[:1], base_url='https://x.test')
        self.assertEqual(_paginas(pdf), 1)

    def test_talla_con_eu_lo_imprime(self):
        par = self.lote.pares.filter(talla__mx_cm=Decimal('26')).first()
        textos = re.findall(rb'\((.*?)\) Tj', pdf_etiquetas([par], base_url='https://x.test'))
        self.assertTrue([t for t in textos if t.startswith(b'EU ')], textos)


class VistasEtiquetasTests(EtiquetasBase):
    def test_lote_todos(self):
        r = self.client.get(reverse('inventario:etiquetas_lote', args=[self.lote.pk]) + '?solo=todos')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(_paginas(r.content), 3)

    def test_pendientes_excluye_en_stock(self):
        p = self.lote.pares.first()
        Par.objects.filter(pk=p.pk).update(estado='en_stock')
        r = self.client.get(reverse('inventario:etiquetas_lote', args=[self.lote.pk]) + '?solo=pendientes')
        self.assertEqual(_paginas(r.content), 2)

    def test_pendientes_sin_pares_404(self):
        self.lote.pares.update(estado='en_stock')
        r = self.client.get(reverse('inventario:etiquetas_lote', args=[self.lote.pk]) + '?solo=pendientes')
        self.assertEqual(r.status_code, 404)

    def test_par_individual_sin_importar_mayusculas(self):
        p = self.lote.pares.first()
        r = self.client.get(reverse('inventario:etiqueta_par', args=[p.codigo.lower()]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(_paginas(r.content), 1)

    def test_par_inexistente_404(self):
        self.assertEqual(self.client.get(reverse('inventario:etiqueta_par', args=['ZZZ-0000'])).status_code, 404)

    def test_solo_staff(self):
        self.client.logout()
        for url in (reverse('inventario:etiquetas_lote', args=[self.lote.pk]),
                    reverse('inventario:etiqueta_par', args=[self.lote.pares.first().codigo])):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn('login', r['Location'])

    def test_botones_del_detalle_apuntan_a_las_urls(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, reverse('inventario:etiquetas_lote', args=[self.lote.pk]) + '?solo=todos')
        self.assertContains(r, reverse('inventario:etiquetas_lote', args=[self.lote.pk]) + '?solo=pendientes')
