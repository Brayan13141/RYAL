import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import Color, Lote, Par
from inventario.services.lotes import crear_lote
from inventario.services.tipos import crear_variante


class LotesVistaBase(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            'st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.base = TipoArticulo(
            nombre='Jordan 4', keywords='jordan 4', costo=Decimal('540'))
        self.base.full_clean()
        self.base.save()
        self.color = Color.objects.create(nombre='negro')
        self.base.colores.add(self.color)
        self.var_a = crear_variante(self.base, 'aereo', costo=Decimal('640'))

    def post_alta(self, **kw):
        datos = {
            'via': 'aereo', 'origen': 'manual', 'referencia': 'R1',
            'lineas': [{'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
                        'cantidades': {'26': 2, '27.5': 1},
                        'precio_proveedor': '360'}]}
        datos.update(kw)
        return self.client.post(
            reverse('inventario:alta_manual'), json.dumps(datos),
            content_type='application/json')


class AltaManualTests(LotesVistaBase):
    def test_get_form(self):
        r = self.client.get(reverse('inventario:alta_manual'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.var_a.nombre)

    def test_post_crea_pares(self):
        r = self.post_alta()
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        lote = Lote.objects.get(pk=data['lote_id'])
        self.assertEqual(lote.pares.count(), 3)
        self.assertEqual(data['url'], reverse('inventario:lote_detalle', args=[lote.pk]))
        self.assertEqual(lote.pares.first().costo, Decimal('640.00'))

    def test_inicial_usa_costo_del_tipo(self):
        r = self.post_alta(origen='inicial')
        lote = Lote.objects.get(pk=r.json()['lote_id'])
        self.assertEqual(lote.pares.first().costo, Decimal('640.00'))
        self.assertIsNone(lote.pares.first().precio_proveedor)

    def test_cantidades_cero_se_ignoran(self):
        r = self.post_alta(lineas=[{
            'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
            'cantidades': {'26': 2, '27': 0, '28': ''}, 'precio_proveedor': '360'}])
        self.assertEqual(Lote.objects.get(pk=r.json()['lote_id']).pares.count(), 2)

    def test_todas_en_cero_400(self):
        r = self.post_alta(lineas=[{
            'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
            'cantidades': {'26': 0}, 'precio_proveedor': '360'}])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])
        self.assertEqual(Lote.objects.count(), 0)

    def test_precio_invalido_400(self):
        r = self.post_alta(lineas=[{
            'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
            'cantidades': {'26': 1}, 'precio_proveedor': 'abc'}])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])

    def test_error_del_servicio_400(self):
        r = self.post_alta(lineas=[{
            'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
            'cantidades': {'26': 1}, 'precio_proveedor': None}])
        self.assertEqual(r.status_code, 400)
        self.assertIn('precio', r.json()['error'])

    def test_precio_no_finito_400(self):
        for malo in ('NaN', 'Infinity'):
            r = self.post_alta(lineas=[{
                'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
                'cantidades': {'26': 1}, 'precio_proveedor': malo}])
            self.assertEqual(r.status_code, 400, malo)
            self.assertFalse(r.json()['ok'])

    def test_linea_o_cantidades_no_dict_400(self):
        for lineas in (['x'], [5], [{'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
                                     'cantidades': [1, 2], 'precio_proveedor': '1'}]):
            r = self.post_alta(lineas=lineas)
            self.assertEqual(r.status_code, 400, lineas)
            self.assertFalse(r.json()['ok'])

    def _post_cant(self, cant):
        # Infinity/NaN no son JSON estándar: se manda el cuerpo a mano.
        cuerpo = ('{"via":"aereo","origen":"manual","referencia":"R","lineas":[{"tipo_id":%d,'
                  '"color_id":%d,"cantidades":{"26":%s},"precio_proveedor":"360"}]}'
                  % (self.var_a.pk, self.color.pk, cant))
        return self.client.post(reverse('inventario:alta_manual'), cuerpo,
                                content_type='application/json')

    def test_cantidad_infinita_400(self):
        r = self._post_cant('Infinity')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])

    def test_cantidad_decimal_400(self):
        r = self._post_cant('2.7')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lote.objects.count(), 0)

    def test_cantidad_excesiva_400(self):
        r = self._post_cant('501')
        self.assertEqual(r.status_code, 400)
        self.assertIn('demasiado grande', r.json()['error'])
        self.assertEqual(self._post_cant('500').status_code, 200)

    def test_referencia_no_string_400(self):
        r = self.post_alta(referencia=5)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])

    def test_json_malformado_400(self):
        r = self.client.post(
            reverse('inventario:alta_manual'), 'no json',
            content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_tipo_colores(self):
        r = self.client.get(reverse('inventario:tipo_colores', args=[self.var_a.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c['id'] for c in r.json()['colores']], [self.color.pk])


class LoteDetalleTests(LotesVistaBase):
    def setUp(self):
        super().setUp()
        from inventario.models import TallaCalzado
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.lote = crear_lote(
            via='aereo', origen='manual', usuario=self.staff, referencia='R1',
            lineas=[{'tipo': self.var_a, 'color': self.color, 'talla': self.t26,
                     'cantidad': 3, 'precio_proveedor': Decimal('360')}])

    def test_contadores(self):
        self.lote.pares.first().transicionar('en_stock', usuario=self.staff, origen='escaner')
        r = self.client.get(reverse('inventario:lote_detalle', args=[self.lote.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['conteo']['en_stock'], 1)
        self.assertEqual(r.context['conteo']['por_recibir'], 2)
        fila = r.context['filas'][0]
        self.assertEqual((fila['esperados'], fila['recibidos']), (3, 1))

    def test_talla_terminada_en_cero_sin_notacion_cientifica(self):
        from inventario.models import TallaCalzado
        t30 = TallaCalzado.objects.get_or_create(mx_cm=Decimal('30'))[0]
        lote = crear_lote(
            via='aereo', origen='manual', usuario=self.staff,
            lineas=[{'tipo': self.var_a, 'color': self.color, 'talla': t30,
                     'cantidad': 1, 'precio_proveedor': Decimal('360')}])
        r = self.client.get(reverse('inventario:lote_detalle', args=[lote.pk]))
        self.assertEqual(r.context['filas'][0]['talla'], '30')
        self.assertNotContains(r, '3E')

    def test_cerrar_deja_faltantes(self):
        r = self.client.post(reverse('inventario:lote_cerrar', args=[self.lote.pk]))
        self.assertEqual(r.status_code, 302)
        self.lote.refresh_from_db()
        self.assertIsNotNone(self.lote.cerrado_at)
        self.assertEqual(self.lote.pares.filter(estado='baja').count(), 3)

    def test_cerrar_requiere_post(self):
        r = self.client.get(reverse('inventario:lote_cerrar', args=[self.lote.pk]))
        self.assertEqual(r.status_code, 405)

    def test_lista(self):
        r = self.client.get(reverse('inventario:lotes'))
        self.assertContains(r, 'R1')


class AccesoTests(LotesVistaBase):
    def test_solo_staff(self):
        self.client.logout()
        u = get_user_model().objects.create_user('n', password='x')
        self.client.force_login(u)
        for name, args in [('alta_manual', []), ('lotes', []),
                           ('lote_detalle', [1]), ('tipo_colores', [1])]:
            r = self.client.get(reverse(f'inventario:{name}', args=args))
            self.assertEqual(r.status_code, 302, name)
        r = self.client.post(reverse('inventario:lote_cerrar', args=[1]))
        self.assertEqual(r.status_code, 302)


class MontosEnormesAltaTests(LotesVistaBase):
    def test_precio_enorme_es_400_no_500(self):
        r = self.post_alta(lineas=[{
            'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
            'cantidades': {'26': 1}, 'precio_proveedor': '1E+12'}])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])
        self.assertEqual(Lote.objects.count(), 0)

    def test_limite_exacto_pasa(self):
        r = self.post_alta(lineas=[{
            'tipo_id': self.var_a.pk, 'color_id': self.color.pk,
            'cantidades': {'26': 1}, 'precio_proveedor': '999999.99'}])
        self.assertEqual(r.status_code, 200, r.content)
