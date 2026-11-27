import io
import re
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from PIL import Image

from inventario.models import Color, FotoColor, Par, PrecioVenta
from inventario.tests.test_descargas_datos import DescargasBase

E = Par.Estado
ENCABEZADOS = ['Modelo', 'Color', 'Talla', 'Calidad', 'Aéreo', 'Marítimo', 'Nacional', 'Total',
               'En camino', 'Costo promedio', 'Mayoreo', 'Menudeo', 'Valor al costo', 'Promo']
URLS = ['inventario:stock_pdf_clientes', 'inventario:stock_pdf_interno', 'inventario:stock_excel_interno']


def textos(pdf):
    """Todos los strings dibujados en el PDF, juntos, decodificados en latin-1."""
    def desescapar(m):  # reportlab escribe los no ASCII como \ddd (octal, WinAnsi)
        return re.sub(rb'\\(\d{3})', lambda o: bytes([int(o.group(1), 8)]), m)
    return '\n'.join(desescapar(m).decode('cp1252') for m in re.findall(rb'\((.*?)\) Tj', pdf))


def jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (40, 30), 'red').save(buf, 'JPEG')
    return buf.getvalue()


class DescargasViewsBase(DescargasBase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(get_user_model().objects.create_user('st', password='x', is_staff=True))
        # Costos 500 + 600: valor 1100; precios de lista 800/1200/500/700.
        self.par(costo='500', talla=self.t27)
        self.par(costo='600', talla=self.t265)
        PrecioVenta.objects.create(tipo=self.base, nombre='Promo', precio=Decimal('950'))
        self.hoy = timezone.localdate().strftime('%d/%m/%Y')

    def pdf(self, nombre, **query):
        resp = self.client.get(reverse(nombre), query)
        self.assertEqual(resp.status_code, 200)
        return resp.content

    def foto_base(self):
        self.base.foto.save('f.jpg', ContentFile(jpeg()), save=True)


class PdfClientesTests(DescargasViewsBase):
    def test_pdf_clientes_sin_cantidades_ni_precios(self):
        t = textos(self.pdf('inventario:stock_pdf_clientes'))
        lineas = t.splitlines()
        self.assertIn('TALLAS DISPONIBLES', t)
        self.assertIn('26.5', lineas)  # cada talla en su propio recuadro
        self.assertIn('27', lineas)
        self.assertLess(lineas.index('26.5'), lineas.index('27'))
        self.assertIn(self.hoy, t)
        self.assertIn('¿Cómo leer este catálogo?', t)
        for prohibido in ('$', '1000', '1100', '550', '800', '1200', '500', '700', '950', 'Total'):
            self.assertNotIn(prohibido, t)

    def test_pdf_clientes_sin_foto_muestra_texto(self):
        self.assertIn('Sin foto', textos(self.pdf('inventario:stock_pdf_clientes')))

    def test_pdf_clientes_con_foto_embebe_imagen(self):
        self.foto_base()
        pdf = self.pdf('inventario:stock_pdf_clientes')
        self.assertIn(b'/Subtype /Image', pdf)
        self.assertNotIn('Sin foto', textos(pdf))

    def test_pdf_foto_con_archivo_borrado_no_truena(self):
        self.foto_base()
        shutil.rmtree(self.media)
        self.assertIn('Sin foto', textos(self.pdf('inventario:stock_pdf_clientes')))

    def test_pdf_foto_ilegible_no_truena(self):
        FotoColor.objects.create(tipo=self.base, color=self.negro,
                                 imagen=ContentFile(b'no es imagen', name='x.jpg'))
        self.assertIn('Sin foto', textos(self.pdf('inventario:stock_pdf_clientes')))

    def test_pdf_acentos(self):
        cafe = Color.objects.create(nombre='cafe')
        Color.objects.filter(pk=cafe.pk).update(nombre='café')  # save() quita acentos; la BD puede traerlos
        self.base.colores.add(cafe)
        self.base.nombre = 'AÑO'
        self.base.save()
        self.par(color=cafe)
        t = textos(self.pdf('inventario:stock_pdf_clientes'))
        self.assertIn('Café', t)
        self.assertIn('AÑO', t)

    def test_pdf_vacio(self):
        for nombre in ('inventario:stock_pdf_clientes', 'inventario:stock_pdf_interno'):
            self.assertIn('Sin pares con estos filtros.', textos(self.pdf(nombre, talla='99')))


class InternoTests(DescargasViewsBase):
    def test_excel_interno_columnas_y_totales(self):
        self.par_m()
        wb = load_workbook(io.BytesIO(self.pdf('inventario:stock_excel_interno')))
        ws = wb['Stock']
        self.assertEqual([c.value for c in ws[1]], ENCABEZADOS)
        filas = {(r[2].value): r for r in ws.iter_rows(min_row=2)}
        self.assertEqual((filas['27'][4].value, filas['27'][5].value), (1, 1))
        self.assertEqual(ws.cell(ws.max_row, 1).value, 'Total general')
        self.assertEqual(ws.cell(ws.max_row, 8).value, 3)
        self.assertIn('Por cantidad', wb.sheetnames)

    def test_excel_texto_no_es_formula(self):
        self.base.nombre = '=1+1'
        self.base.save()
        ws = load_workbook(io.BytesIO(self.pdf('inventario:stock_excel_interno')))['Stock']
        self.assertEqual(ws['A2'].value, '=1+1')
        self.assertEqual(ws['A2'].data_type, 's')

    def test_pdf_interno_trae_costos(self):
        t = textos(self.pdf('inventario:stock_pdf_interno'))
        self.assertTrue('1100' in t or '1,100' in t, t)
        self.assertIn(self.hoy, t)
        self.assertIn('Promo', t)


class DisenoInternoTests(DescargasViewsBase):
    def test_pdf_interno_guia_resumen_y_subtotal(self):
        t = textos(self.pdf('inventario:stock_pdf_interno'))
        lineas = t.splitlines()
        self.assertIn('Cómo leer las tablas', t)
        self.assertIn('PARES EN EL CUARTO', lineas)
        self.assertIn('$1,100.00', lineas)  # resumen: valor al costo de los 2 pares
        self.assertIn('Subtotal', lineas)
        self.assertIn('Valor al costo', lineas)  # encabezado completo, sin abreviar
        self.assertIn('TOTAL GENERAL', lineas)

    def test_cero_se_muestra_como_raya(self):
        # Los 2 pares son aéreos: Marítimo y Nacional van en cero → "—".
        self.assertIn('—', textos(self.pdf('inventario:stock_pdf_interno')).splitlines())

    def test_foto_del_modelo_prefiere_la_base(self):
        from inventario.reportes_stock import _foto_modelo
        FotoColor.objects.create(tipo=self.base, color=self.negro,
                                 imagen=ContentFile(jpeg(), name='c.jpg'))
        filas = [{'foto': FotoColor.objects.get().imagen}]
        self.assertEqual(_foto_modelo(self.base, filas).name, filas[0]['foto'].name)
        self.foto_base()
        self.assertEqual(_foto_modelo(self.base, filas).name, self.base.foto.name)

    def test_tabla_larga_repite_encabezado_en_cada_pagina(self):
        from inventario.models import TallaCalzado
        for i in range(60):
            self.par(talla=TallaCalzado.objects.create(mx_cm=Decimal(f'{30 + i / 2}'), orden=200 + i))
        pdf = self.pdf('inventario:stock_pdf_interno')
        paginas = pdf.count(b'/Type /Page\n') or pdf.count(b'/Type /Page ')
        lineas = textos(pdf).splitlines()
        self.assertGreater(lineas.count('Página 2'), 0)
        self.assertGreaterEqual(lineas.count('Costo prom.'), 2)


class InternoAnchoTests(DescargasViewsBase):
    def test_pdf_interno_no_corta_montos_con_muchos_precios(self):
        nombres = ['Precio Facebook', 'Precio Instagram Mayorista', 'Distribuidor Zona Norte',
                   'Cliente Frecuente', 'Liquidación', 'Tiktok Shop', 'Amazon', 'Mercado Libre',
                   'Evento Especial']
        for n in nombres:
            PrecioVenta.objects.create(tipo=self.base, nombre=n, precio=Decimal('125000'))
        self.par(costo='125000')
        t = textos(self.pdf('inventario:stock_pdf_interno'))
        self.assertIn('125,000.00', t)
        lineas = t.splitlines()
        for n in nombres + ['Promo']:
            self.assertTrue(
                n in lineas or any(l.endswith('…') and len(l) > 3 and n.startswith(l[:-1])
                                   for l in lineas), n)

    def test_excel_encabezado_precio_venta_formula_es_texto(self):
        PrecioVenta.objects.create(tipo=self.base, nombre='=Promo', precio=Decimal('1'))
        ws = load_workbook(io.BytesIO(self.pdf('inventario:stock_excel_interno')))['Stock']
        celdas = [c for c in ws[1] if c.value == '=Promo']
        self.assertEqual(len(celdas), 1)
        self.assertEqual(celdas[0].data_type, 's')


class TallasEnvueltasTests(DescargasViewsBase):
    def test_envolver_respeta_el_ancho(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from inventario.reportes_stock import _envolver
        items = [f'{30 + i / 2:g}' for i in range(20)]
        lineas = _envolver('Tallas: ', items, 200, 'Helvetica', 11)
        self.assertGreater(len(lineas), 1)
        for l in lineas:
            self.assertLessEqual(stringWidth(l, 'Helvetica', 11), 200)
        self.assertEqual(' '.join(' '.join(lineas).replace('Tallas:', '').split()),
                         ' '.join(items))

    def test_pdf_clientes_con_20_tallas_aparecen_todas(self):
        from inventario.models import TallaCalzado
        for i in range(20):
            t = TallaCalzado.objects.create(mx_cm=Decimal(f'{30 + i / 2}'), orden=100 + i)
            self.par(talla=t)
        t = textos(self.pdf('inventario:stock_pdf_clientes'))
        for i in range(20):
            self.assertRegex(t, rf'(^|\s){30 + i / 2:g}(\s|$)')


class VistasTests(DescargasViewsBase):
    def test_vistas_attachment_y_nombre(self):
        hoy = timezone.localdate().isoformat()
        esperado = {'inventario:stock_pdf_clientes': f'stock-clientes-{hoy}.pdf',
                    'inventario:stock_pdf_interno': f'stock-interno-{hoy}.pdf',
                    'inventario:stock_excel_interno': f'stock-interno-{hoy}.xlsx'}
        for nombre, archivo in esperado.items():
            resp = self.client.get(reverse(nombre))
            self.assertEqual(resp['Content-Disposition'], f'attachment; filename="{archivo}"')
        self.assertIn('spreadsheetml', self.client.get(reverse(URLS[2]))['Content-Type'])

    def test_vista_filtro_basura_400(self):
        for nombre in URLS:
            self.assertEqual(self.client.get(reverse(nombre), {'modelo': 'abc'}).status_code, 400)

    def test_vistas_no_staff_redirige(self):
        self.client.logout()
        self.client.force_login(get_user_model().objects.create_user('u', password='x'))
        for nombre in URLS:
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 302)
