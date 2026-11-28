"""El costo de una variante creada al vuelo sale de un dato real, nunca adivinado.

Antes: costo = costo del modelo − flete aéreo + flete de la vía. En el lote #7
(inventario inicial nacional) eso dejó cada par $230 abajo de su costo real.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import Color, Lote, Par, TallaCalzado
from inventario.services.editar_lote import version_lote
from inventario.services.lotes import crear_lote
from inventario.services.tipos import crear_variante


class CostoVarianteNuevaBase(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        # Un modelo nacional de $400 sin variante N: el caso del lote #7.
        self.base = TipoArticulo(nombre='Timberlan tactica', keywords='timberlan tactica',
                                 costo=Decimal('400'))
        self.base.full_clean()
        self.base.save()
        self.color = Color.objects.create(nombre='negro')
        self.base.colores.add(self.color)

    def linea(self, **kw):
        datos = {'tipo_id': self.base.pk, 'color_id': self.color.pk, 'cantidades': {'26': 2}}
        datos.update(kw)
        return datos

    def alta(self, origen, lineas, **kw):
        datos = {'via': 'nacional', 'origen': origen, 'lineas': lineas}
        datos.update(kw)
        return self.client.post(reverse('inventario:alta_manual'), json.dumps(datos),
                                content_type='application/json')

    def variante(self):
        return self.base.variante('nacional')


class AltaInicialTests(CostoVarianteNuevaBase):
    def test_sin_costo_por_par_no_crea_nada(self):
        r = self.alta('inicial', [self.linea()])
        self.assertEqual(r.status_code, 400)
        self.assertIn('costo por par', r.json()['error'])
        self.assertIsNone(self.variante())
        self.assertFalse(Lote.objects.exists())

    def test_con_costo_por_par_la_variante_y_los_pares_lo_toman(self):
        r = self.alta('inicial', [self.linea(costo_par='400')])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.variante().costo, Decimal('400'))
        self.assertEqual(set(Par.objects.values_list('costo', flat=True)), {Decimal('400')})

    def test_variante_existente_usa_su_costo(self):
        crear_variante(self.base, 'nacional', costo=Decimal('380'))
        r = self.alta('inicial', [self.linea(costo_par='999')])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.variante().costo, Decimal('380'))
        self.assertEqual(set(Par.objects.values_list('costo', flat=True)), {Decimal('380')})

    def test_costo_por_par_invalido(self):
        r = self.alta('inicial', [self.linea(costo_par='abc')])
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.variante())


class AltaLlegadaTests(CostoVarianteNuevaBase):
    def test_variante_nueva_cuesta_proveedor_mas_flete_de_la_via(self):
        # Tarifa nacional sembrada: $50. Antes salía 400 − 280 + 50 = 170.
        r = self.alta('manual', [self.linea(precio_proveedor='350')])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.variante().costo, Decimal('400'))

    def test_usa_el_flete_capturado_del_lote(self):
        r = self.alta('manual', [self.linea(precio_proveedor='350')], flete_por_par='70')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.variante().costo, Decimal('420'))

    def test_sin_precio_no_crea_la_variante(self):
        r = self.alta('manual', [self.linea()])
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.variante())


class EditarLoteVarianteNuevaTests(CostoVarianteNuevaBase):
    def setUp(self):
        super().setUp()
        # Un lote nacional que ya existe, con otro modelo que sí tiene variante N.
        otro = TipoArticulo(nombre='VEJA', keywords='veja', costo=Decimal('320'))
        otro.full_clean()
        otro.save()
        otro.colores.add(self.color)
        var = crear_variante(otro, 'nacional', costo=Decimal('320'))
        t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        self.inicial = crear_lote(via='nacional', origen='inicial', usuario=self.staff, lineas=[
            {'tipo': var, 'color': self.color, 'talla': t26, 'cantidad': 1}])
        self.manual = crear_lote(via='nacional', origen='manual', usuario=self.staff,
                                 flete_por_par=Decimal('60'), lineas=[
            {'tipo': var, 'color': self.color, 'talla': t26, 'cantidad': 1,
             'precio_proveedor': Decimal('270')}])

    def editar(self, lote, lineas):
        datos = {'version': version_lote(lote), 'lineas': lineas}
        return self.client.post(reverse('inventario:lote_editar', args=[lote.pk]),
                                json.dumps(datos), content_type='application/json')

    def test_lote_inicial_pide_costo_por_par(self):
        r = self.editar(self.inicial, [self.linea()])
        self.assertEqual(r.status_code, 400)
        self.assertIn('costo por par', r.json()['error'])
        self.assertIsNone(self.variante())

    def test_lote_inicial_con_costo_por_par(self):
        r = self.editar(self.inicial, [self.linea(costo_par='400')])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.variante().costo, Decimal('400'))

    def test_lote_con_proveedor_usa_el_flete_del_lote(self):
        r = self.editar(self.manual, [self.linea(precio_proveedor='350')])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.variante().costo, Decimal('410'))


class PantallasTests(CostoVarianteNuevaBase):
    def test_alta_tiene_campo_costo_por_par(self):
        r = self.client.get(reverse('inventario:alta_manual'))
        self.assertContains(r, 'i-costo')
        self.assertContains(r, 'costo_par')

    def test_editar_lote_inicial_tiene_campo_costo_por_par(self):
        t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        var = crear_variante(self.base, 'aereo', costo=Decimal('640'))
        lote = crear_lote(via='aereo', origen='inicial', usuario=self.staff, lineas=[
            {'tipo': var, 'color': self.color, 'talla': t26, 'cantidad': 1}])
        r = self.client.get(reverse('inventario:lote_editar', args=[lote.pk]))
        self.assertContains(r, 'i-costo')

    def test_modelos_y_vias_explica_el_proveedor_sugerido(self):
        r = self.client.get(reverse('inventario:tipos_convertir'))
        self.assertContains(r, 'js-prov-origen')
        self.assertContains(r, 'flete aéreo')
        self.assertContains(r, 'revísalo')
