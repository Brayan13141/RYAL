"""Detalle del lote en cards por modelo, con filtro por modelo y la lista de no escaneados."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.models import Color, Par, TallaCalzado
from inventario.services.lotes import crear_lote
from inventario.services.tipos import crear_variante


def _base(nombre, kw, color):
    b = TipoArticulo(nombre=nombre, keywords=kw, costo=Decimal('540'))
    b.full_clean()
    b.save()
    b.colores.add(color)
    return b


class LoteCardsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('st', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.negro = Color.objects.create(nombre='negro')
        self.jordan = crear_variante(_base('Jordan 4', 'jordan 4', self.negro), 'aereo',
                                     costo=Decimal('640'))
        self.yeezy = crear_variante(_base('Yeezy', 'yeezy', self.negro), 'aereo',
                                    costo=Decimal('580'))
        t26 = TallaCalzado.objects.get(mx_cm=Decimal('26'))
        t27 = TallaCalzado.objects.get(mx_cm=Decimal('27'))
        linea = lambda tipo, talla, n: {'tipo': tipo, 'color': self.negro, 'talla': talla,
                                        'cantidad': n, 'precio_proveedor': Decimal('300')}
        self.lote = crear_lote(via='aereo', origen='manual', usuario=self.staff, lineas=[
            linea(self.jordan, t26, 2), linea(self.jordan, t27, 1), linea(self.yeezy, t26, 2)])
        self.orden = list(self.lote.pares.order_by('pk'))
        # Recibido el primero de cada modelo: quedan 2 Jordan y 1 Yeezy sin escanear.
        for par in (self.orden[0], self.orden[3]):
            par.transicionar('en_stock', usuario=self.staff, origen='escaner')
        self.url = reverse('inventario:lote_detalle', args=[self.lote.pk])

    def test_modelos_agrupan_sus_renglones_con_totales(self):
        modelos = self.client.get(self.url).context['modelos']
        self.assertEqual([m['tipo'] for m in modelos], [self.jordan.nombre, self.yeezy.nombre])
        jordan = modelos[0]
        self.assertEqual(jordan['tipo_id'], self.jordan.pk)
        self.assertEqual((jordan['esperados'], jordan['recibidos'], jordan['pendientes']), (3, 1, 2))
        self.assertEqual(len(jordan['filas']), 2)
        self.assertEqual(sum(f['esperados'] for f in jordan['filas']), 3)

    def test_no_escaneados_con_su_numero_de_etiqueta(self):
        lista = self.client.get(self.url).context['no_escaneados']
        pendientes = [p for p in self.orden if p.pk not in (self.orden[0].pk, self.orden[3].pk)]
        self.assertEqual([p['codigo'] for p in lista], [p.codigo for p in pendientes])
        # El número es la posición en "Imprimir todo" (orden por pk, base 1).
        self.assertEqual([p['numero'] for p in lista], [2, 3, 5])
        self.assertEqual(lista[0]['tipo_id'], self.jordan.pk)
        self.assertEqual(lista[0]['color'], 'negro')
        self.assertEqual(lista[0]['talla'], '26')
        self.assertEqual(lista[0]['url'], reverse('inventario:par_ficha', args=[pendientes[0].codigo]))

    def test_no_escaneados_excluye_vendidos_y_bajas(self):
        self.orden[1].transicionar('baja', usuario=self.staff, origen='cierre_lote', motivo='faltante')
        lista = self.client.get(self.url).context['no_escaneados']
        self.assertNotIn(self.orden[1].codigo, [p['codigo'] for p in lista])

    def test_pagina_trae_filtro_cards_y_seccion_de_no_escaneados(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'class="inv-filtro-chip js-filtro"', count=2)
        self.assertContains(r, f'data-modelo="{self.jordan.pk}"')
        self.assertContains(r, 'class="inv-lote-card js-card"', count=2)
        self.assertContains(r, 'id="pend-box"')
        self.assertContains(r, 'data-max-lista="30"')
        self.assertContains(r, 'id="no-escaneados-json"')
        # Los renglones siguen siendo los campos que lee la vista de etiquetas.
        self.assertContains(r, 'class="form-check-input m-0 js-chk"', count=3)
        self.assertContains(r, 'class="form-check-input m-0 js-grupo"', count=2)
