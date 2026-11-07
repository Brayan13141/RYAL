"""Tercera vía (nacional, MODA IA) y flete por par capturado al crear el lote."""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.etiquetas import pdf_etiquetas
from inventario.models import Color, Lote, Par, TallaCalzado, TarifaFlete
from inventario.services.bot import faltantes_de_texto
from inventario.services.importar import analizar_csv, confirmar_importacion
from inventario.services.lotes import crear_lote
from inventario.services.stock import matriz_stock
from inventario.services.tipos import crear_variante, sugerir_prefijo

from .test_vistas_importar import _firmar

CSV = (b'tarjeta,modelo,color,talla_mx,cantidad,precio_proveedor\n'
       b'1,Jordan 4,negro,26,2,300\n')


class Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.base = TipoArticulo(nombre='Jordan 4', keywords='jordan 4', costo=Decimal('540'))
        self.base.full_clean()
        self.base.save()
        self.negro = Color.objects.create(nombre='negro')
        self.base.colores.add(self.negro)
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))

    def variante(self, via='nacional', costo='350'):
        return crear_variante(self.base, via, costo=Decimal(costo))

    def linea(self, tipo, precio='300', cantidad=2):
        return {'tipo': tipo, 'color': self.negro, 'talla': self.t26,
                'cantidad': cantidad, 'precio_proveedor': Decimal(precio)}


class ViaNacionalTests(Base):
    def test_tarifa_nacional_sembrada_en_50(self):
        self.assertEqual(TarifaFlete.objects.get(via='nacional').monto_por_par, Decimal('50'))

    def test_variante_nacional_nombre_keywords_y_prefijo(self):
        v = self.variante()
        self.assertEqual(v.nombre, 'Jordan 4 N')
        self.assertEqual(v.keywords_list, ['jordan 4 nacional'])
        self.assertTrue(v.prefijo.endswith('N'))
        self.assertEqual(self.base.variante('nacional'), v)

    def test_sugerir_prefijo_nacional(self):
        self.assertEqual(sugerir_prefijo('Jordan 4', 'nacional'), 'JD4N')

    def test_keyword_de_variante_nacional_debe_terminar_en_nacional(self):
        v = self.variante()
        v.keywords = 'jordan 4 n'
        with self.assertRaises(ValidationError):
            v.full_clean()

    def test_lote_nacional_usa_la_tarifa(self):
        lote = crear_lote(via='nacional', origen='manual', usuario=self.user,
                          lineas=[self.linea(self.variante())])
        self.assertEqual(lote.flete_por_par, Decimal('50'))
        self.assertEqual(lote.pares.first().costo, Decimal('350.00'))

    def test_lote_nacional_rechaza_variante_aerea(self):
        with self.assertRaises(ValueError):
            crear_lote(via='nacional', origen='manual', usuario=self.user,
                       lineas=[self.linea(self.variante('aereo', '640'))])

    def test_stock_cuenta_la_via_nacional(self):
        crear_lote(via='nacional', origen='manual', usuario=self.user,
                   lineas=[self.linea(self.variante())])
        Par.objects.update(estado='en_stock')
        celda = matriz_stock()[0]['colores'][0]['tallas'][Decimal('26.0')]
        self.assertEqual(celda['nacional'], 2)
        self.assertEqual(celda['aereo'], 0)

    def test_etiqueta_imprime_nacional(self):
        lote = crear_lote(via='nacional', origen='manual', usuario=self.user,
                          lineas=[self.linea(self.variante(), cantidad=1)])
        pdf = pdf_etiquetas(lote.pares.all(), base_url='https://x.test')
        self.assertIn(b'NACIONAL', pdf)

    def test_bot_pregunta_por_las_tres_vias(self):
        self.variante()
        self.base.refresh_from_db()
        msg = faltantes_de_texto('jordan 4 negro 26', self.base)[0]['mensaje']
        self.assertIn('nacional', msg)


class FleteCapturadoTests(Base):
    def test_flete_capturado_reemplaza_la_tarifa(self):
        lote = crear_lote(via='nacional', origen='manual', usuario=self.user,
                          lineas=[self.linea(self.variante())], flete_por_par=Decimal('42.5'))
        self.assertEqual(lote.flete_por_par, Decimal('42.50'))
        par = lote.pares.first()
        self.assertEqual(par.flete, Decimal('42.50'))
        self.assertEqual(par.costo, Decimal('342.50'))

    def test_flete_capturado_tambien_en_aereo(self):
        lote = crear_lote(via='aereo', origen='manual', usuario=self.user,
                          lineas=[self.linea(self.variante('aereo', '640'))],
                          flete_por_par=Decimal('250'))
        self.assertEqual(lote.pares.first().costo, Decimal('550.00'))

    def test_flete_negativo_se_rechaza_sin_crear_nada(self):
        with self.assertRaises(ValueError):
            crear_lote(via='nacional', origen='manual', usuario=self.user,
                       lineas=[self.linea(self.variante())], flete_por_par=Decimal('-1'))
        self.assertFalse(Lote.objects.exists())

    def test_flete_capturado_no_cambia_la_tarifa(self):
        crear_lote(via='nacional', origen='manual', usuario=self.user,
                   lineas=[self.linea(self.variante())], flete_por_par=Decimal('40'))
        self.assertEqual(TarifaFlete.objects.get(via='nacional').monto_por_par, Decimal('50'))

    def test_csv_costo_nuevo_usa_el_flete_capturado(self):
        self.variante()
        a = analizar_csv(CSV, via='nacional', flete=Decimal('45'))
        self.assertEqual(a.flete, Decimal('45.00'))
        self.assertEqual(a.cambios_costo[0].costo_nuevo, Decimal('345.00'))

    def test_csv_sin_flete_usa_la_tarifa(self):
        self.variante()
        self.assertEqual(analizar_csv(CSV, via='nacional').flete, Decimal('50.00'))

    def test_csv_confirmar_guarda_el_flete_capturado(self):
        self.variante()
        lote = confirmar_importacion(CSV, via='nacional', usuario=self.user, flete=Decimal('45'))
        self.assertEqual(lote.flete_por_par, Decimal('45.00'))
        self.assertEqual(lote.pares.first().costo, Decimal('345.00'))


class VistasTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def alta(self, **extra):
        v = self.variante()
        datos = {'via': 'nacional', 'origen': 'manual', 'referencia': 'MODA IA',
                 'lineas': [{'tipo_id': v.pk, 'color_id': self.negro.pk,
                             'cantidades': {'26': 2}, 'precio_proveedor': '300'}], **extra}
        return self.client.post(reverse('inventario:alta_manual'), json.dumps(datos),
                                content_type='application/json')

    def test_alta_manual_nacional_con_flete(self):
        r = self.alta(flete_por_par='48')
        self.assertTrue(r.json()['ok'], r.content)
        self.assertEqual(Lote.objects.get().flete_por_par, Decimal('48.00'))

    def test_alta_manual_sin_flete_usa_la_tarifa(self):
        self.assertTrue(self.alta().json()['ok'])
        self.assertEqual(Lote.objects.get().flete_por_par, Decimal('50.00'))

    def test_alta_manual_flete_invalido_400(self):
        r = self.alta(flete_por_par='abc')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Lote.objects.exists())

    def test_alta_manual_ofrece_nacional_y_tarifas(self):
        html = self.client.get(reverse('inventario:alta_manual')).content.decode()
        self.assertIn('value="nacional"', html)
        self.assertIn('id="f-flete"', html)

    def test_importar_con_flete_llega_al_lote(self):
        self.variante()
        r = self.client.post(reverse('inventario:importar'), {
            'archivo': SimpleUploadedFile('l.csv', CSV, content_type='text/csv'),
            'via': 'nacional', 'referencia': 'R', 'flete': '44'})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse('inventario:importar_confirmar'),
                             {'token': r.context['token']})
        self.assertEqual(Lote.objects.get().flete_por_par, Decimal('44.00'))

    def test_importar_flete_invalido_no_avanza(self):
        r = self.client.post(reverse('inventario:importar'), {
            'archivo': SimpleUploadedFile('l.csv', CSV, content_type='text/csv'),
            'via': 'nacional', 'referencia': 'R', 'flete': '-3'})
        self.assertNotIn('token', r.context)

    def test_token_viejo_sin_flete_sigue_sirviendo(self):
        self.variante()
        r = self.client.post(reverse('inventario:importar_confirmar'),
                             {'token': _firmar(CSV, via='nacional')})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Lote.objects.get().flete_por_par, Decimal('50.00'))

    def test_stock_filtra_por_nacional(self):
        r = self.client.get(reverse('inventario:stock'), {'via': 'nacional'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="inv-filtro-chip is-on" href="?estado=en_stock&amp;via=nacional"')
