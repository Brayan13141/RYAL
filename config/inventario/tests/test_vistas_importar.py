import base64
import time
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import Color, Lote, Par, TallaCalzado
from inventario.services.tipos import crear_variante

CABECERA = 'tarjeta,modelo,color,talla_mx,cantidad,precio_proveedor\n'


def _firmar(contenido, via='aereo', ref='R1'):
    return signing.dumps(
        {'b64': base64.b64encode(contenido).decode(), 'via': via, 'ref': ref},
        salt='inventario-import')


class ImportarVistaBase(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.negro = Color.objects.create(nombre='negro')
        self.jordan = TipoArticulo(nombre='Jordan 4', keywords='jordan 4', costo=Decimal('540'))
        self.jordan.full_clean()
        self.jordan.save()
        self.jordan.colores.add(self.negro)
        self.v_jordan = crear_variante(self.jordan, 'aereo', costo=Decimal('640'))

    def csv(self, *renglones):
        return (CABECERA + '\n'.join(renglones) + '\n').encode('utf-8')

    def bueno(self):
        return self.csv('1,Jordan 4,negro,26,2,300')

    def subir(self, contenido, via='aereo', ref='R1', nombre='llegada.csv'):
        return self.client.post(reverse('inventario:importar'), {
            'archivo': SimpleUploadedFile(nombre, contenido, content_type='text/csv'),
            'via': via, 'referencia': ref})


class PreviewTests(ImportarVistaBase):
    def test_solo_staff(self):
        self.client.logout()
        for nombre in ('importar', 'importar_preview', 'importar_confirmar',
                       'importar_alta_color', 'importar_alta_variante',
                       'importar_alta_talla'):
            r = self.client.get(reverse(f'inventario:{nombre}'))
            self.assertEqual(r.status_code, 302, nombre)
        self.client.force_login(get_user_model().objects.create_user('n', password='x'))
        self.assertEqual(self.client.get(reverse('inventario:importar')).status_code, 302)

    def test_get_form(self):
        r = self.client.get(reverse('inventario:importar'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="archivo"')

    def test_preview_no_escribe_nada(self):
        r = self.subir(self.bueno())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Jordan 4')
        self.assertContains(r, reverse('inventario:importar_confirmar'))
        self.assertEqual(Par.objects.count(), 0)
        self.assertEqual(Lote.objects.count(), 0)
        self.v_jordan.refresh_from_db()
        self.assertEqual(self.v_jordan.costo, Decimal('640'))

    def test_preview_muestra_cambio_de_costo(self):
        # Flete aéreo 280: 300 + 280 = 580 (el costo actual es 640).
        r = self.subir(self.bueno())
        self.assertContains(r, '640')
        self.assertContains(r, '580')

    def test_preview_con_faltantes_no_ofrece_confirmar(self):
        r = self.subir(self.csv('1,Dunk Low,rojo,30,1,300'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Dunk Low')
        self.assertNotContains(r, reverse('inventario:importar_confirmar'))

    def test_archivo_mayor_a_1mb_se_rechaza(self):
        r = self.subir(b'x' * (1024 * 1024 + 1))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '1 MB')
        self.assertNotContains(r, reverse('inventario:importar_confirmar'))

    def test_via_invalida_se_rechaza(self):
        r = self.subir(self.bueno(), via='terrestre')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, reverse('inventario:importar_confirmar'))

    def test_preview_desde_token(self):
        r = self.client.post(reverse('inventario:importar_preview'),
                             {'token': _firmar(self.bueno())})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, reverse('inventario:importar_confirmar'))

    def test_preview_token_alterado_400(self):
        r = self.client.post(reverse('inventario:importar_preview'),
                             {'token': _firmar(self.bueno()) + 'x'})
        self.assertEqual(r.status_code, 400)


class ConfirmarTests(ImportarVistaBase):
    def test_firma_valida_crea_lote_y_redirige(self):
        r = self.client.post(reverse('inventario:importar_confirmar'),
                             {'token': _firmar(self.bueno())})
        lote = Lote.objects.get()
        self.assertRedirects(r, reverse('inventario:lote_detalle', args=[lote.pk]))
        self.assertEqual(lote.origen, 'csv')
        self.assertEqual(lote.referencia, 'R1')
        self.assertEqual(lote.pares.count(), 2)

    def test_firma_alterada_400(self):
        r = self.client.post(reverse('inventario:importar_confirmar'),
                             {'token': _firmar(self.bueno()) + 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lote.objects.count(), 0)

    def test_sin_token_400(self):
        r = self.client.post(reverse('inventario:importar_confirmar'), {})
        self.assertEqual(r.status_code, 400)

    def test_firma_vencida_400_con_mensaje(self):
        token = _firmar(self.bueno())
        with mock.patch('time.time', return_value=time.time() + 3601):
            r = self.client.post(reverse('inventario:importar_confirmar'), {'token': token})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, 'venci', status_code=400)
        self.assertEqual(Lote.objects.count(), 0)

    def test_get_no_permitido(self):
        self.assertEqual(self.client.get(reverse('inventario:importar_confirmar')).status_code, 405)

    def test_csv_con_faltantes_400_y_no_escribe(self):
        r = self.client.post(reverse('inventario:importar_confirmar'),
                             {'token': _firmar(self.csv('1,Dunk Low,rojo,30,1,300'))})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lote.objects.count(), 0)


class AltaRapidaTests(ImportarVistaBase):
    def post(self, nombre, datos, contenido=None, via='aereo'):
        datos = dict(datos, token=_firmar(contenido or self.bueno(), via=via))
        return self.client.post(reverse(f'inventario:{nombre}'), datos)

    def test_color_se_crea_y_se_asigna_al_modelo(self):
        r = self.post('importar_alta_color', {'color': 'Rojo', 'modelo': 'Jordan 4'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['ok'])
        rojo = Color.objects.get(nombre='rojo')
        self.assertIn(rojo, self.jordan.colores.all())

    def test_color_existente_solo_se_asigna(self):
        verde = Color.objects.create(nombre='verde')
        r = self.post('importar_alta_color', {'color': 'VERDE', 'modelo': 'Jordan 4'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Color.objects.filter(nombre='verde').count(), 1)
        self.assertIn(verde, self.jordan.colores.all())

    def test_color_sin_modelo_solo_crea(self):
        r = self.post('importar_alta_color', {'color': 'Rojo'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Color.objects.filter(nombre='rojo').exists())

    def test_color_vacio_400(self):
        r = self.post('importar_alta_color', {'color': '  '})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])

    def test_color_modelo_inexistente_400(self):
        r = self.post('importar_alta_color', {'color': 'rojo', 'modelo': 'Nada'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Color.objects.filter(nombre='rojo').exists())

    def test_variante_se_crea_con_la_via_del_token(self):
        r = self.post('importar_alta_variante', {'modelo': 'Jordan 4'}, via='maritimo')
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(self.jordan.variante('maritimo'))

    def test_variante_modelo_inexistente_400(self):
        r = self.post('importar_alta_variante', {'modelo': 'Nada'})
        self.assertEqual(r.status_code, 400)

    def test_variante_error_de_validacion_400(self):
        with mock.patch('inventario.views.importar.crear_variante',
                        side_effect=ValidationError('malo')):
            r = self.post('importar_alta_variante', {'modelo': 'Jordan 4'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])

    def test_talla_se_crea_en_su_posicion(self):
        TallaCalzado.objects.all().delete()
        TallaCalzado.objects.create(mx_cm=Decimal('25'), orden=1)
        TallaCalzado.objects.create(mx_cm=Decimal('27'), orden=2)
        r = self.post('importar_alta_talla', {'mx_cm': '26,5'})
        self.assertEqual(r.status_code, 200)
        orden = list(TallaCalzado.objects.values_list('mx_cm', flat=True))
        self.assertEqual(orden, [Decimal('25.0'), Decimal('26.5'), Decimal('27.0')])

    def test_talla_duplicada_o_invalida_400(self):
        TallaCalzado.objects.get_or_create(mx_cm=Decimal('26'))
        self.assertEqual(self.post('importar_alta_talla', {'mx_cm': '26'}).status_code, 400)
        self.assertEqual(self.post('importar_alta_talla', {'mx_cm': 'abc'}).status_code, 400)
        self.assertEqual(self.post('importar_alta_talla', {'mx_cm': '99'}).status_code, 400)

    def test_token_invalido_400(self):
        r = self.client.post(reverse('inventario:importar_alta_color'),
                             {'color': 'rojo', 'token': 'basura'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Color.objects.filter(nombre='rojo').exists())
