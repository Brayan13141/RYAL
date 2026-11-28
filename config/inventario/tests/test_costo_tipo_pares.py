"""Editar el costo de un tipo ofrece actualizar el costo de sus pares sin vender.

El 2026-11 se corrigió el costo de modelos en Tipos y los pares ya creados se
quedaron con el costo viejo: cada venta posterior grabó una ganancia falsa.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import Color, MovimientoPar, Par, TallaCalzado
from inventario.services.lotes import crear_lote
from inventario.services.tipos import crear_variante


class CostoTipoParesTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.base = TipoArticulo(nombre='Timberlan tactica', keywords='timberlan tactica',
                                 costo=Decimal('400'))
        self.base.full_clean()
        self.base.save()
        self.color = Color.objects.create(nombre='negro')
        self.base.colores.add(self.color)
        self.var = crear_variante(self.base, 'nacional', costo=Decimal('170'))
        self.t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        linea = {'tipo': self.var, 'color': self.color, 'talla': self.t26, 'cantidad': 2}
        self.inicial = crear_lote(via='nacional', origen='inicial', usuario=self.staff,
                                  lineas=[dict(linea)])
        self.manual = crear_lote(via='nacional', origen='manual', usuario=self.staff,
                                 lineas=[dict(linea, precio_proveedor=Decimal('120'))])
        # Un par vendido no se toca: su venta ya grabó su costo.
        self.vendido = self.inicial.pares.first()
        self.vendido.transicionar('en_stock', usuario=self.staff, origen='escaner')
        self.vendido.transicionar('vendido', usuario=self.staff, origen='escaner')

    def _post(self, costo, **extra):
        datos = {
            'nombre': self.var.nombre, 'keywords': self.var.keywords, 'costo': costo,
            'padre': self.base.pk, 'via': 'nacional', 'usa_tallas': 'on',
            'colores': [self.color.pk], 'prefijo': self.var.prefijo}
        datos.update(extra)
        return self.client.post(reverse('negocio:tipo_edit', args=[self.var.pk]), datos)

    def _costos(self, lote):
        return sorted(lote.pares.exclude(pk=self.vendido.pk).values_list('costo', flat=True))

    def test_cambiar_costo_pide_confirmacion_y_no_guarda(self):
        r = self._post('400')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'pares sin vender')
        self.assertContains(r, 'name="pares_costo" value="actualizar"')
        self.assertContains(r, 'name="pares_costo" value="no"')
        self.var.refresh_from_db()
        self.assertEqual(self.var.costo, Decimal('170'))

    def test_actualizar_pares_sin_vender(self):
        r = self._post('400', pares_costo='actualizar')
        self.assertRedirects(r, reverse('negocio:tipos_list'), fetch_redirect_response=False)
        self.var.refresh_from_db()
        self.assertEqual(self.var.costo, Decimal('400'))
        self.assertEqual(self._costos(self.inicial), [Decimal('400')])
        self.assertEqual(self._costos(self.manual), [Decimal('400'), Decimal('400')])
        # Con proveedor: costo = proveedor + flete sigue valiendo; el flete no cambia.
        for par in self.manual.pares.all():
            self.assertEqual(par.flete, Decimal('50'))
            self.assertEqual(par.precio_proveedor, Decimal('350'))
        # Inventario inicial: sin proveedor ni flete.
        par_ini = self.inicial.pares.exclude(pk=self.vendido.pk).get()
        self.assertIsNone(par_ini.precio_proveedor)
        self.vendido.refresh_from_db()
        self.assertEqual(self.vendido.costo, Decimal('170'))
        notas = MovimientoPar.objects.filter(origen='edicion', nota__startswith='Costo actualizado')
        self.assertEqual(notas.count(), 3)
        self.assertIn('170.00 → 400.00', notas.first().nota)

    def test_guardar_solo_el_tipo(self):
        self._post('400', pares_costo='no')
        self.var.refresh_from_db()
        self.assertEqual(self.var.costo, Decimal('400'))
        self.assertEqual(self._costos(self.inicial), [Decimal('170')])
        self.assertEqual(self._costos(self.manual), [Decimal('170'), Decimal('170')])

    def test_sin_cambiar_costo_guarda_directo(self):
        r = self._post('170', nombre='Timberlan tactica N')
        self.assertEqual(r.status_code, 302)
        self.var.refresh_from_db()
        self.assertEqual(self.var.nombre, 'Timberlan tactica N')

    def test_costo_menor_que_el_flete_no_toca_esos_pares(self):
        self._post('30', pares_costo='actualizar')
        # Inicial: el costo es el total, sí se actualiza; con proveedor quedaría negativo.
        self.assertEqual(self._costos(self.inicial), [Decimal('30')])
        self.assertEqual(self._costos(self.manual), [Decimal('170'), Decimal('170')])

    def test_sin_pares_con_otro_costo_no_pregunta(self):
        Par.objects.exclude(pk=self.vendido.pk).update(costo=Decimal('400'))
        r = self._post('400')
        self.assertEqual(r.status_code, 302)
