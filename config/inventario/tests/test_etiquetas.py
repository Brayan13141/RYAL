import re
from decimal import Decimal
from unittest.mock import patch

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
    def test_una_pagina_por_par_y_tamano_54x40(self):
        pdf = pdf_etiquetas(self.lote.pares.all(), base_url='https://x.test')
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(_paginas(pdf), 3)
        cajas = re.findall(rb'/MediaBox\s*\[\s*([\d.\s]+?)\s*\]', pdf)
        self.assertEqual(len(cajas), 3)
        for caja in cajas:
            x0, y0, w, h = (float(n) for n in caja.split())
            self.assertEqual((x0, y0), (0.0, 0.0))
            self.assertAlmostEqual(w, 153.07, delta=0.01)
            self.assertAlmostEqual(h, 113.39, delta=0.01)  # 40 mm: menos papel del rollo

    def test_talla_sin_eu_no_rompe_y_omite_el_dato(self):
        par = self.lote.pares.get(talla__mx_cm=Decimal('25.5'))
        pdf = pdf_etiquetas([par], base_url='https://x.test')
        self.assertEqual(_paginas(pdf), 1)
        # Solo los textos dibujados (el PDF trae el QR como binario).
        textos = re.findall(rb'\((.*?)\) Tj', pdf)
        self.assertIn(b'25.5', textos)
        # startswith y no `in`: el código del par es aleatorio y puede contener "EU".
        self.assertFalse([t for t in textos if t.startswith(b'EU ') or b'None' in t], textos)

    def test_nombre_largo_no_rompe(self):
        self.base.nombre = 'Modelo con un nombre larguisimo que no cabe jamas en la etiqueta'
        self.base.save()
        pdf = pdf_etiquetas(self.lote.pares.all()[:1], base_url='https://x.test')
        self.assertEqual(_paginas(pdf), 1)

    def test_talla_con_eu_lo_imprime(self):
        par = self.lote.pares.filter(talla__mx_cm=Decimal('26')).first()
        textos = re.findall(rb'\((.*?)\) Tj', pdf_etiquetas([par], base_url='https://x.test'))
        self.assertTrue([t for t in textos if t.startswith(b'EU ')], textos)


_FUENTES = {b'F1': 'Helvetica', b'F2': 'Helvetica-Bold'}


def _textos(pdf):
    """{texto: (tam, x, y, fuente)} de cada string dibujado, con la fuente vigente."""
    vigente, salida = (None, None), {}
    for m in re.finditer(rb'/(F\d) ([\d.]+) Tf|1 0 0 1 ([\d.]+) ([\d.]+) Tm \((.*?)\) Tj', pdf):
        if m.group(1):
            vigente = (_FUENTES[m.group(1)], float(m.group(2)))
        else:
            texto = m.group(5).decode('latin-1')
            salida[texto] = (vigente[1], float(m.group(3)), float(m.group(4)), vigente[0])
    return salida


class DisenoEtiquetaTests(EtiquetasBase):
    """Fuentes legibles y equivalencias pegadas debajo del código (feedback de la PM290)."""

    def setUp(self):
        super().setUp()
        self.par = self.lote.pares.filter(talla__mx_cm=Decimal('26')).first()
        self.t = _textos(pdf_etiquetas([self.par], base_url='https://x.test'))
        self.eq = next(k for k in self.t if k.startswith('EU '))

    def test_equivalencias_justo_debajo_del_codigo(self):
        y_codigo, y_eq = self.t[self.par.codigo][2], self.t[self.eq][2]
        self.assertGreater(y_codigo, y_eq)
        self.assertLessEqual(y_codigo - y_eq, 14)

    def test_fuentes_mas_grandes(self):
        self.assertGreaterEqual(self.t[self.par.codigo][0], 9)
        self.assertGreaterEqual(self.t[self.eq][0], 8)
        self.assertGreaterEqual(self.t['26'][0], 28)
        self.assertGreaterEqual(self.t['NEGRO'][0], 8)
        # reportlab escribe la É como escape octal (A\311REO).
        via = next(k for k in self.t if k.startswith('A') and k.endswith('REO'))
        self.assertGreaterEqual(self.t[via][0], 7.5)
        self.assertGreaterEqual(self.t['Jordan 4'][0], 9)

    def test_color_largo_va_en_dos_lineas_sin_recortar(self):
        self.color.nombre = 'negro total/suela blanca'
        self.color.save()
        par = Par.objects.select_related('tipo__padre', 'color', 'talla').get(pk=self.par.pk)
        t = _textos(pdf_etiquetas([par], base_url='https://x.test'))
        lineas = [k for k in t if 'NEGRO' in k or 'SUELA' in k or 'BLANCA' in k]
        self.assertEqual(''.join(lineas).replace(' ', ''), 'NEGROTOTAL/SUELABLANCA', lineas)
        for linea in lineas:
            self.assertGreaterEqual(t[linea][0], 7, linea)

    def test_margen_inferior_minimo_bajo_las_tallas(self):
        from reportlab.lib.units import mm
        y_eq = self.t[self.eq][2]
        self.assertGreaterEqual(y_eq, 3 * mm)   # que la impresora no la corte
        self.assertLessEqual(y_eq, 7 * mm)      # sin desperdiciar rollo

    def test_ningun_texto_se_sale_de_la_etiqueta(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from inventario.etiquetas import ALTO, ANCHO
        for nombre in ('Modelo con un nombre larguisimo que no cabe jamas', 'Jordan 4'):
            self.base.nombre = nombre
            self.base.save()
            par = Par.objects.select_related('tipo__padre', 'color', 'talla').get(pk=self.par.pk)
            for texto, (tam, x, y, fuente) in _textos(
                    pdf_etiquetas([par], base_url='https://x.test')).items():
                ancho = stringWidth(texto, fuente, tam)
                self.assertGreaterEqual(x, 0, texto)
                self.assertLessEqual(x + ancho, ANCHO + 0.01, texto)
                self.assertGreaterEqual(y, 0, texto)
                self.assertLessEqual(y + tam, ALTO + 0.01, texto)


class SeleccionEtiquetasTests(EtiquetasBase):
    """Imprimir solo N etiquetas de cada grupo tipo×color×talla del lote."""

    def setUp(self):
        super().setUp()
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.t255 = TallaCalzado.objects.get(mx_cm=Decimal('25.5'))
        self.url = reverse('inventario:etiquetas_lote', args=[self.lote.pk])

    def campo(self, talla):
        return f'c_{self.var_a.pk}_{self.color.pk}_{talla.pk}'

    def pedir(self, **datos):
        return self.client.get(self.url, datos)

    def test_una_sola_etiqueta_de_un_grupo(self):
        r = self.pedir(**{self.campo(self.t26): '1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_paginas(r.content), 1)

    def test_varios_grupos(self):
        r = self.pedir(**{self.campo(self.t26): '2', self.campo(self.t255): '1'})
        self.assertEqual(_paginas(r.content), 3)

    def test_vacio_y_cero_no_imprimen_ese_grupo(self):
        r = self.pedir(**{self.campo(self.t26): '1', self.campo(self.t255): ''})
        self.assertEqual(_paginas(r.content), 1)
        r = self.pedir(**{self.campo(self.t26): '0', self.campo(self.t255): '1'})
        self.assertEqual(_paginas(r.content), 1)

    def test_mas_de_los_que_hay_se_topa(self):
        r = self.pedir(**{self.campo(self.t26): '5'})
        self.assertEqual(_paginas(r.content), 2)

    def test_seleccion_con_solo_pendientes(self):
        p = self.lote.pares.filter(talla=self.t26).first()
        Par.objects.filter(pk=p.pk).update(estado='en_stock')
        r = self.pedir(**{self.campo(self.t26): '2', 'solo': 'pendientes'})
        self.assertEqual(_paginas(r.content), 1)

    def test_nada_seleccionado_404(self):
        r = self.pedir(**{self.campo(self.t26): '0', self.campo(self.t255): ''})
        self.assertEqual(r.status_code, 404)

    def test_cantidad_invalida_400(self):
        for valor in ('abc', '-1', '1.5', '²'):
            r = self.pedir(**{self.campo(self.t26): valor})
            self.assertEqual(r.status_code, 400, valor)

    def test_cantidad_invalida_no_refleja_html(self):
        r = self.pedir(**{self.campo(self.t26): '<script>alert(1)</script>'})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r['Content-Type'].startswith('text/plain'), r['Content-Type'])
        self.assertNotIn(b'<script>', r.content)

    def test_no_imprime_pares_de_otro_lote(self):
        otro = crear_lote(
            via='aereo', origen='manual', usuario=self.staff,
            lineas=[{'tipo': self.var_a, 'color': self.color, 'talla': self.t26,
                     'cantidad': 3, 'precio_proveedor': Decimal('360')}])
        r = self.pedir(**{self.campo(self.t26): '5'})
        self.assertEqual(_paginas(r.content), 2)
        self.assertFalse(any(p.codigo.encode() in r.content for p in otro.pares.all()))


@patch('inventario.etiquetas.MAX_POR_PDF', 2)
class PartesEtiquetasTests(EtiquetasBase):
    """La app de la PM290 acepta como mucho MAX_POR_PDF páginas: ?parte=k da el
    k-ésimo trozo de la misma lista (todo, pendientes o selección)."""

    def setUp(self):
        super().setUp()
        self.url = reverse('inventario:etiquetas_lote', args=[self.lote.pk])
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.t255 = TallaCalzado.objects.get(mx_cm=Decimal('25.5'))

    def codigos(self, r):
        return [p.codigo for p in self.lote.pares.all() if p.codigo.encode() in r.content]

    def test_todo_en_partes(self):
        r1 = self.client.get(self.url, {'solo': 'todos', 'parte': '1'})
        r2 = self.client.get(self.url, {'solo': 'todos', 'parte': '2'})
        self.assertEqual((_paginas(r1.content), _paginas(r2.content)), (2, 1))
        self.assertFalse(set(self.codigos(r1)) & set(self.codigos(r2)))
        self.assertEqual(len(set(self.codigos(r1)) | set(self.codigos(r2))), 3)

    def test_seleccion_en_partes_respeta_el_orden(self):
        sel = {f'c_{self.var_a.pk}_{self.color.pk}_{self.t255.pk}': '1',
               f'c_{self.var_a.pk}_{self.color.pk}_{self.t26.pk}': '2'}
        r1 = self.client.get(self.url, {**sel, 'parte': '1'})
        r2 = self.client.get(self.url, {**sel, 'parte': '2'})
        self.assertEqual((_paginas(r1.content), _paginas(r2.content)), (2, 1))
        solo_255 = self.lote.pares.get(talla=self.t255).codigo
        self.assertIn(solo_255, self.codigos(r1))

    def test_nombre_del_archivo_lleva_la_parte(self):
        r = self.client.get(self.url, {'solo': 'todos', 'parte': '2', 'descargar': '1'})
        self.assertEqual(r['Content-Disposition'],
                         f'attachment; filename="etiquetas-lote-{self.lote.pk}-parte-2.pdf"')

    def test_parte_fuera_de_rango_404(self):
        self.assertEqual(self.client.get(self.url, {'solo': 'todos', 'parte': '3'}).status_code, 404)

    def test_parte_invalida_400(self):
        for valor in ('0', '-1', 'x', '1.5'):
            r = self.client.get(self.url, {'solo': 'todos', 'parte': valor})
            self.assertEqual(r.status_code, 400, valor)

    def test_sin_parte_y_dentro_del_limite_sigue_igual(self):
        r = self.client.get(self.url, {f'c_{self.var_a.pk}_{self.color.pk}_{self.t26.pk}': '2'})
        self.assertEqual(_paginas(r.content), 2)

    def test_sin_parte_y_sobre_el_limite_da_solo_la_primera(self):
        """Nunca sale un PDF que la app no pueda imprimir."""
        r = self.client.get(self.url, {'solo': 'todos'})
        self.assertEqual(_paginas(r.content), 2)

    def test_detalle_parte_los_botones_de_arriba(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, f'href="{self.url}?solo=todos&amp;parte=1"')
        self.assertContains(r, f'href="{self.url}?solo=todos&amp;parte=2"')
        self.assertNotContains(r, f'href="{self.url}?solo=todos&amp;parte=3"')
        self.assertContains(r, f'href="{self.url}?solo=todos&amp;parte=2&amp;descargar=1"')
        self.assertContains(r, 'Etiquetas 1–2')
        self.assertContains(r, 'Etiquetas 3–3')

    def test_detalle_trae_checks_y_selectores(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, 'data-limite="2"')
        self.assertContains(r, 'id="sel-todo"')
        self.assertContains(r, 'id="sel-limite"')
        self.assertContains(r, 'Seleccionar 2')
        self.assertContains(r, 'class="form-check-input m-0 js-chk"', count=2)
        self.assertContains(r, 'data-max="2"')


class IntervaloEtiquetasTests(EtiquetasBase):
    """?desde=N&hasta=M: de la etiqueta N a la M (base 1) de la misma lista."""

    def setUp(self):
        super().setUp()
        self.url = reverse('inventario:etiquetas_lote', args=[self.lote.pk])
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.orden = [p.codigo for p in self.lote.pares.order_by('pk')]

    def codigos(self, r):
        return [c for c in self.orden if c.encode() in r.content]

    def test_intervalo_sobre_todo_el_lote(self):
        r = self.client.get(self.url, {'desde': '2', 'hasta': '3'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.codigos(r), self.orden[1:3])

    def test_solo_desde_o_solo_hasta(self):
        self.assertEqual(self.codigos(self.client.get(self.url, {'desde': '3'})), self.orden[2:])
        self.assertEqual(self.codigos(self.client.get(self.url, {'hasta': '1'})), self.orden[:1])

    def test_campos_vacios_del_formulario_toman_todo_el_lote(self):
        """El formulario manda todos los c_* vacíos: con intervalo no es "nada marcado"."""
        r = self.client.get(self.url, {f'c_{self.var_a.pk}_{self.color.pk}_{self.t26.pk}': '',
                                       'desde': '1', 'hasta': '2'})
        self.assertEqual(self.codigos(r), self.orden[:2])

    def test_intervalo_recorta_la_seleccion(self):
        r = self.client.get(self.url, {f'c_{self.var_a.pk}_{self.color.pk}_{self.t26.pk}': '2',
                                       'desde': '2'})
        self.assertEqual(_paginas(r.content), 1)

    @patch('inventario.etiquetas.MAX_POR_PDF', 1)
    def test_intervalo_en_partes(self):
        r = self.client.get(self.url, {'desde': '2', 'hasta': '3', 'parte': '2'})
        self.assertEqual(self.codigos(r), [self.orden[2]])
        self.assertEqual(self.client.get(self.url, {'desde': '2', 'hasta': '3', 'parte': '3'}).status_code, 404)

    def test_fuera_del_lote_404(self):
        self.assertEqual(self.client.get(self.url, {'desde': '4'}).status_code, 404)

    def test_invalido_400(self):
        for datos in ({'desde': '0'}, {'hasta': 'x'}, {'desde': '1.5'}, {'desde': '3', 'hasta': '2'}):
            self.assertEqual(self.client.get(self.url, datos).status_code, 400, datos)

    def test_nombre_del_archivo_lleva_el_intervalo(self):
        r = self.client.get(self.url, {'desde': '2', 'descargar': '1'})
        self.assertEqual(r['Content-Disposition'],
                         f'attachment; filename="etiquetas-lote-{self.lote.pk}-2-a-fin.pdf"')

    def test_detalle_trae_intervalo_y_check_por_modelo(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, 'name="desde"')
        self.assertContains(r, 'name="hasta"')
        self.assertContains(r, 'data-total="3"')
        self.assertContains(r, 'class="form-check-input m-0 js-grupo"', count=1)
        self.assertContains(r, f'data-grupo="{self.var_a.pk}"', count=4)


class DescargarEtiquetasTests(EtiquetasBase):
    """descargar=1 manda el PDF como archivo; sin él se abre en la pestaña."""

    def setUp(self):
        super().setUp()
        self.url = reverse('inventario:etiquetas_lote', args=[self.lote.pk])

    def test_por_defecto_se_abre_inline(self):
        r = self.client.get(self.url + '?solo=todos')
        self.assertTrue(r['Content-Disposition'].startswith('inline;'), r['Content-Disposition'])

    def test_lote_descargar(self):
        r = self.client.get(self.url, {'solo': 'todos', 'descargar': '1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Disposition'],
                         f'attachment; filename="etiquetas-lote-{self.lote.pk}.pdf"')
        self.assertEqual(_paginas(r.content), 3)

    def test_seleccion_descargar(self):
        t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        r = self.client.get(self.url, {
            f'c_{self.var_a.pk}_{self.color.pk}_{t26.pk}': '1', 'descargar': '1'})
        self.assertTrue(r['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(_paginas(r.content), 1)

    def test_par_descargar(self):
        p = self.lote.pares.first()
        r = self.client.get(reverse('inventario:etiqueta_par', args=[p.codigo]), {'descargar': '1'})
        self.assertEqual(r['Content-Disposition'], f'attachment; filename="etiqueta-{p.codigo}.pdf"')

    def test_botones_de_descarga(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, f'href="{self.url}?solo=todos&amp;descargar=1"')
        self.assertContains(r, f'href="{self.url}?solo=pendientes&amp;descargar=1"')
        self.assertContains(r, 'name="descargar" value="1"')
        p = self.lote.pares.first()
        r = self.client.get(reverse('inventario:par_ficha', args=[p.codigo]))
        self.assertContains(
            r, f'href="{reverse("inventario:etiqueta_par", args=[p.codigo])}?descargar=1"')


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

    def test_detalle_trae_el_form_de_seleccion(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        url = reverse('inventario:etiquetas_lote', args=[self.lote.pk])
        self.assertContains(r, f'action="{url}"')
        talla = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.assertContains(r, f'name="c_{self.var_a.pk}_{self.color.pk}_{talla.pk}"')

    def test_botones_del_detalle_apuntan_a_las_urls(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, reverse('inventario:etiquetas_lote', args=[self.lote.pk]) + '?solo=todos')
        self.assertContains(r, reverse('inventario:etiquetas_lote', args=[self.lote.pk]) + '?solo=pendientes')


class PendientesPrimeroTests(EtiquetasBase):
    """Al pedir N etiquetas de un renglón salen primero las de los pares sin escanear;
    cada card del lote trae un botón para imprimir solo lo que le falta."""

    def setUp(self):
        super().setUp()
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.url = reverse('inventario:etiquetas_lote', args=[self.lote.pk])
        self.campo = f'c_{self.var_a.pk}_{self.color.pk}_{self.t26.pk}'
        # El primero (por pk) ya se escaneó: el pendiente es el segundo.
        self.escaneado, self.pendiente = self.lote.pares.filter(talla=self.t26).order_by('pk')
        Par.objects.filter(pk=self.escaneado.pk).update(estado='en_stock')

    def impresos(self, datos):
        with patch('inventario.views.etiquetas.etiquetas.pdf_etiquetas',
                   return_value=b'%PDF') as pdf:
            r = self.client.get(self.url, datos)
        self.assertEqual(r.status_code, 200)
        return [p.codigo for p in pdf.call_args[0][0]]

    def test_una_etiqueta_toma_el_pendiente_no_el_escaneado(self):
        self.assertEqual(self.impresos({self.campo: '1'}), [self.pendiente.codigo])

    def test_si_piden_mas_que_los_pendientes_siguen_los_escaneados(self):
        self.assertEqual(self.impresos({self.campo: '2'}),
                         [self.pendiente.codigo, self.escaneado.codigo])

    def test_por_modelo_solo_imprime_sus_pendientes(self):
        otro = crear_variante(
            TipoArticulo.objects.create(nombre='Nike 270', keywords='nike 270', costo=Decimal('500')),
            'aereo', costo=Decimal('600'))
        otro.padre.colores.add(self.color)
        lote2 = crear_lote(via='aereo', origen='manual', usuario=self.staff, lineas=[
            {'tipo': otro, 'color': self.color, 'talla': self.t26, 'cantidad': 1,
             'precio_proveedor': Decimal('300')}])
        lote2.pares.update(lote=self.lote)
        codigos = self.impresos({'solo': 'pendientes', 'modelo': str(self.var_a.pk)})
        esperados = list(self.lote.pares.filter(tipo=self.var_a, estado='por_recibir')
                         .order_by('pk').values_list('codigo', flat=True))
        self.assertEqual(codigos, esperados)
        self.assertNotIn(self.escaneado.codigo, codigos)

    def test_modelo_invalido_400(self):
        r = self.client.get(self.url, {'solo': 'pendientes', 'modelo': 'x'})
        self.assertEqual(r.status_code, 400)

    def test_la_card_trae_el_boton_de_sus_pendientes(self):
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, f'href="{self.url}?solo=pendientes&amp;modelo={self.var_a.pk}"')

    def test_la_card_completa_no_trae_el_boton(self):
        self.lote.pares.update(estado='en_stock')
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertNotContains(r, f'modelo={self.var_a.pk}')
