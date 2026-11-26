from decimal import Decimal

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase

from catalog.models import TipoArticulo
from inventario.models import Lote, Par, PrecioLista, PrecioVenta
from inventario.services import cantidad
from inventario.services.descargas import (
    Filtro, FiltroInvalido, datos_clientes, datos_internos, filtro_de_query)
from inventario.tests.test_stock import StockBase

E = Par.Estado
SIN_FILTRO = Filtro(frozenset(), frozenset(), frozenset(), frozenset())


def filtro(**kw):
    campos = {'modelos': frozenset(), 'tallas': frozenset(), 'colores': frozenset(), 'vias': frozenset()}
    campos.update({k: frozenset(v) for k, v in kw.items()})
    return Filtro(**campos)


class DescargasBase(StockBase):
    def setUp(self):
        super().setUp()
        self.nacional = TipoArticulo.objects.create(
            nombre='Jordan 4 N', keywords='jordan 4 nacional', costo=Decimal('300'),
            padre=self.base, via='nacional', usa_tallas=True, prefijo='JD4N')
        self.lote_n = Lote.objects.create(
            referencia='LN', origen='manual', via='nacional', flete_por_par=Decimal('0'))
        PrecioLista.objects.update_or_create(
            calidad='importado', tipo_venta='mayoreo', defaults={'precio': Decimal('800')})
        PrecioLista.objects.update_or_create(
            calidad='importado', tipo_venta='menudeo', defaults={'precio': Decimal('1200')})
        PrecioLista.objects.update_or_create(
            calidad='nacional', tipo_venta='mayoreo', defaults={'precio': Decimal('500')})
        PrecioLista.objects.update_or_create(
            calidad='nacional', tipo_venta='menudeo', defaults={'precio': Decimal('700')})

    def par_n(self, estado=E.EN_STOCK, color=None, talla=None, costo='300'):
        self._n += 1
        return Par.objects.create(
            codigo=f'JD4N-{self._n:04d}', tipo=self.nacional, color=color or self.negro,
            talla=talla or self.t27, lote=self.lote_n, estado=estado, costo=Decimal(costo))


class FiltroTests(TestCase):
    def test_filtro_de_query_listas_y_normaliza(self):
        q = QueryDict('modelo=1&modelo=2&color=%20Negro&talla=27&via=aereo&talla=')
        self.assertEqual(filtro_de_query(q), Filtro(
            frozenset({1, 2}), frozenset({'27'}), frozenset({'negro'}), frozenset({'aereo'})))

    def test_filtro_invalido(self):
        with self.assertRaises(FiltroInvalido):
            filtro_de_query(QueryDict('modelo=abc'))
        with self.assertRaises(FiltroInvalido):
            filtro_de_query(QueryDict('via=avion'))


class ClientesTests(DescargasBase):
    def test_clientes_solo_en_stock_y_sin_numeros(self):
        self.par(talla=self.t27)
        self.par(talla=self.t265)
        self.par(talla=self.t27, color=self.marino, estado=E.POR_RECIBIR)
        grupos = datos_clientes(SIN_FILTRO)
        self.assertEqual(len(grupos), 1)
        g = grupos[0]
        self.assertEqual((g['base'], g['color']), (self.base, 'negro'))
        self.assertEqual(g['tallas'], ['26.5', '27'])
        self.assertIsNone(g['foto'])
        self.assertEqual(set(g), {'base', 'color', 'foto', 'tallas'})

    def test_clientes_filtro_talla_restringe_tallas(self):
        self.par(talla=self.t27)
        self.par(talla=self.t265)
        self.par(talla=self.t265, color=self.marino)
        grupos = datos_clientes(filtro(tallas={'27'}))
        self.assertEqual([(g['color'], g['tallas']) for g in grupos], [('negro', ['27'])])

    def test_clientes_filtro_via_y_color(self):
        self.par()
        self.par_m(color=self.marino)
        self.par_n()
        self.assertEqual(
            [g['color'] for g in datos_clientes(filtro(vias={'maritimo'}))], ['marino'])
        self.assertEqual(
            [g['color'] for g in datos_clientes(filtro(colores={'negro'}))], ['negro'])
        self.assertEqual(len(datos_clientes(filtro(modelos={self.base.pk + 999}))), 0)


class InternosTests(DescargasBase):
    def fila(self, datos, calidad, talla='27', color='negro'):
        return next(f for f in datos['filas']
                    if (f['calidad'], f['talla'], f['color']) == (calidad, talla, color))

    def test_interno_parte_por_calidad(self):
        self.par()
        self.par_m()
        self.par_n()
        d = datos_internos(SIN_FILTRO)
        self.assertEqual(len(d['filas']), 2)
        imp = self.fila(d, 'importado')
        self.assertEqual(imp['por_via'], {'aereo': 1, 'maritimo': 1, 'nacional': 0})
        self.assertEqual(imp['total'], 2)
        self.assertEqual((imp['mayoreo'], imp['menudeo']), (Decimal('800'), Decimal('1200')))
        nac = self.fila(d, 'nacional')
        self.assertEqual(nac['por_via'], {'aereo': 0, 'maritimo': 0, 'nacional': 1})
        self.assertEqual((nac['mayoreo'], nac['menudeo']), (Decimal('500'), Decimal('700')))
        self.assertEqual(d['totales']['total'], 3)

    def test_interno_costo_no_suma_flete(self):
        self.par(costo='1000')
        self.par(costo='1200')
        Par.objects.update(flete=Decimal('150'))
        f = datos_internos(SIN_FILTRO)['filas'][0]
        self.assertEqual(f['costo_promedio'], Decimal('1100'))
        self.assertEqual(f['valor_costo'], Decimal('2200'))

    def test_interno_aereo_y_maritimo_mismo_grupo_promedia_y_suma(self):
        self.par(costo='1000')
        p = self.par_m()
        Par.objects.filter(pk=p.pk).update(costo=Decimal('1300'))
        d = datos_internos(SIN_FILTRO)
        self.assertEqual(len(d['filas']), 1)
        f = d['filas'][0]
        self.assertEqual(f['calidad'], 'importado')
        self.assertEqual(f['costo_promedio'], Decimal('1150'))
        self.assertEqual(f['valor_costo'], Decimal('2300'))

    def test_interno_lote_inicial_flete_none(self):
        self.par()
        Par.objects.update(flete=None)
        self.assertEqual(len(datos_internos(SIN_FILTRO)['filas']), 1)

    def test_interno_en_camino_columna_aparte(self):
        self.par(costo='1000')
        self.par(estado=E.POR_RECIBIR, costo='9999')
        self.par(estado=E.POR_RECIBIR, talla=self.t265)
        d = datos_internos(SIN_FILTRO)
        f = self.fila(d, 'importado')
        self.assertEqual((f['total'], f['en_camino'], f['valor_costo']), (1, 1, Decimal('1000')))
        solo_camino = self.fila(d, 'importado', talla='26.5')
        self.assertEqual((solo_camino['total'], solo_camino['en_camino']), (0, 1))
        self.assertIsNone(solo_camino['costo_promedio'])
        self.assertEqual(solo_camino['valor_costo'], Decimal('0'))
        self.assertEqual(d['totales'], {'total': 1, 'en_camino': 2, 'valor_costo': Decimal('1000')})
        self.assertEqual([f['talla'] for f in d['filas']], ['26.5', '27'])

    def test_interno_precios_venta_columnas(self):
        PrecioVenta.objects.create(tipo=self.base, nombre='Promo', precio=Decimal('900'))
        self.par()
        d = datos_internos(SIN_FILTRO)
        self.assertEqual(d['nombres_precio_venta'], ['Promo'])
        self.assertEqual(d['filas'][0]['precios_venta']['Promo'], Decimal('900'))

    def test_interno_por_cantidad_ignora_chips(self):
        gorra = TipoArticulo.objects.create(
            nombre='Gorra', keywords='gorra', costo=Decimal('50'))
        u = get_user_model().objects.create_user('u', password='x')
        cantidad.activar(gorra, 10, usuario=u)
        self.par()
        d = datos_internos(filtro(colores={'marino'}, vias={'nacional'}))
        self.assertEqual(d['filas'], [])
        self.assertEqual([x['tipo'] for x in d['por_cantidad']], [gorra])
        x = d['por_cantidad'][0]
        self.assertEqual((x['cantidad'], x['costo'], x['valor']), (10, Decimal('50'), Decimal('500')))
