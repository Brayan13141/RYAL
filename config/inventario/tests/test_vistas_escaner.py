import json
import re

from django.contrib.auth import get_user_model
from django.urls import reverse

from inventario.tests.test_lotes import LotesBase


class EscanerBase(LotesBase):
    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_user(
            'st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.lote = self.crear()
        self.par = self.lote.pares.first()

    def post_json(self, nombre, datos, **kw):
        return self.client.post(
            reverse(nombre, **kw), json.dumps(datos), content_type='application/json')


class EscanerPantallaTests(EscanerBase):
    def test_get_lista_lotes_y_preselecciona(self):
        r = self.client.get(reverse('inventario:escanear'), {'lote': self.lote.pk})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Recibir')
        self.assertContains(r, 'tab-contar')
        self.assertEqual(r.context['lote_sel'], self.lote.pk)

    def test_lote_inexistente_no_rompe(self):
        r = self.client.get(reverse('inventario:escanear'), {'lote': 'zzz'})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['lote_sel'])


class RecibirEndpointTests(EscanerBase):
    def test_verde(self):
        r = self.post_json('inventario:escanear_recibir',
                           {'lectura': self.par.codigo.lower(), 'lote_id': self.lote.pk})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['nivel'], 'verde')
        self.assertEqual(d['codigo'], self.par.codigo)
        self.assertTrue(d['movimiento_id'])
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'en_stock')

    def test_amarillo_y_rojo(self):
        url = 'inventario:escanear_recibir'
        self.post_json(url, {'lectura': self.par.codigo, 'lote_id': self.lote.pk})
        d = self.post_json(url, {'lectura': self.par.codigo, 'lote_id': self.lote.pk}).json()
        self.assertEqual(d['nivel'], 'amarillo')
        d = self.post_json(url, {'lectura': 'NOEXISTE-1', 'lote_id': self.lote.pk}).json()
        self.assertEqual(d['nivel'], 'rojo')
        self.assertIsNone(d['codigo'])

    def test_sin_lote_o_lote_desconocido_es_400(self):
        r = self.post_json('inventario:escanear_recibir', {'lectura': self.par.codigo})
        self.assertEqual(r.status_code, 400)
        self.assertIn('lote', r.json()['error'].lower())
        r = self.post_json('inventario:escanear_recibir',
                           {'lectura': self.par.codigo, 'lote_id': 99999})
        self.assertEqual(r.status_code, 400)

    def test_json_invalido_es_400(self):
        r = self.client.post(reverse('inventario:escanear_recibir'), 'no-json',
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_get_no_permitido(self):
        self.assertEqual(
            self.client.get(reverse('inventario:escanear_recibir')).status_code, 405)


class DeshacerEndpointTests(EscanerBase):
    def test_deshacer_con_nota(self):
        mid = self.post_json('inventario:escanear_recibir',
                             {'lectura': self.par.codigo, 'lote_id': self.lote.pk}
                             ).json()['movimiento_id']
        r = self.post_json('inventario:deshacer', {'movimiento_id': mid, 'nota': 'error'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['nivel'], 'verde')
        self.par.refresh_from_db()
        self.assertEqual(self.par.estado, 'por_recibir')

    def test_sin_nota_es_rojo(self):
        mid = self.post_json('inventario:escanear_recibir',
                             {'lectura': self.par.codigo, 'lote_id': self.lote.pk}
                             ).json()['movimiento_id']
        d = self.post_json('inventario:deshacer', {'movimiento_id': mid, 'nota': ' '}).json()
        self.assertEqual(d['nivel'], 'rojo')

    def test_movimiento_invalido_es_400(self):
        r = self.post_json('inventario:deshacer', {'movimiento_id': 'x', 'nota': 'n'})
        self.assertEqual(r.status_code, 400)


class FichaTests(EscanerBase):
    def test_ficha_historial_en_orden_y_minusculas(self):
        self.post_json('inventario:escanear_recibir',
                       {'lectura': self.par.codigo, 'lote_id': self.lote.pk})
        r = self.client.get(
            reverse('inventario:par_ficha', args=[self.par.codigo.lower()]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.par.codigo)
        self.assertContains(r, reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertContains(r, reverse('inventario:etiqueta_par', args=[self.par.codigo]))

    def test_historial_en_orden_cronologico(self):
        recibir = lambda: self.post_json(  # noqa: E731
            'inventario:escanear_recibir',
            {'lectura': self.par.codigo, 'lote_id': self.lote.pk}).json()
        mid = recibir()['movimiento_id']
        self.post_json('inventario:deshacer', {'movimiento_id': mid, 'nota': 'nota-uno'})
        recibir()
        r = self.client.get(reverse('inventario:par_ficha', args=[self.par.codigo]))
        html = r.content.decode()
        tabla = html[html.index('<tbody>'):html.index('</tbody>')]
        origenes = re.findall(r'<td[^>]*>(Alta|Escáner|Deshacer)</td>', tabla)
        self.assertEqual(origenes, ['Alta', 'Escáner', 'Deshacer', 'Escáner'])
        self.assertIn('nota-uno', tabla)
        # Etiquetas legibles, no códigos crudos.
        self.assertNotIn('por_recibir', tabla)
        self.assertIn('Por recibir', tabla)

    def test_ficha_sin_precio_muestra_guion(self):
        lote = self.crear(origen='inicial', lineas=[self.linea(precio=None)])
        par = lote.pares.first()
        r = self.client.get(reverse('inventario:par_ficha', args=[par.codigo]))
        self.assertContains(r, '—')

    def test_ficha_inexistente_404(self):
        self.assertEqual(
            self.client.get(reverse('inventario:par_ficha', args=['NOPE-0000'])).status_code,
            404)

    def test_ruta_del_qr(self):
        self.assertEqual(
            reverse('inventario:par_ficha', args=['AB-CDEF']),
            '/panel/inventario/p/AB-CDEF/')


class StaffOnlyTests(EscanerBase):
    def test_anonimo_redirige_a_login(self):
        self.client.logout()
        par = self.par.codigo
        for nombre, args in [('inventario:escanear', []),
                             ('inventario:escanear_recibir', []),
                             ('inventario:deshacer', []),
                             ('inventario:par_ficha', [par])]:
            r = self.client.get(reverse(nombre, args=args))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertIn('login', r['Location'], nombre)

    def test_no_staff_redirige(self):
        self.client.force_login(
            get_user_model().objects.create_user('u', password='x'))
        r = self.client.get(reverse('inventario:par_ficha', args=[self.par.codigo]))
        self.assertEqual(r.status_code, 302)
        r = self.client.post(reverse('inventario:escanear_recibir'), '{}',
                             content_type='application/json')
        self.assertEqual(r.status_code, 302)
