from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.models import TipoArticulo
from inventario.models import Color, Lote, Par, TallaCalzado
from inventario.services.importar import analizar_csv, confirmar_importacion
from inventario.services.tipos import crear_variante

FIXTURES = Path(__file__).parent / 'fixtures'
CABECERA = 'tarjeta,modelo,color,talla_mx,cantidad,precio_proveedor\n'


def _base(nombre, keywords, color):
    tipo = TipoArticulo(nombre=nombre, keywords=keywords, costo=Decimal('500'))
    tipo.full_clean()
    tipo.save()
    tipo.colores.add(color)
    return tipo


class ImportarBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('staff', password='x')
        self.negro = Color.objects.create(nombre='negro', alias='black')
        self.blanco = Color.objects.create(nombre='blanco')
        self.jordan = _base('Jordan 4', 'jordan 4', self.negro)
        self.jordan.colores.add(self.blanco)
        self.dunk = _base('Nike Dunk', 'nike dunk', self.negro)
        self.yeezy = _base('Yeezy 350', 'yeezy 350', self.blanco)
        self.v_jordan = crear_variante(self.jordan, 'aereo', costo=Decimal('640'))
        self.v_dunk = crear_variante(self.dunk, 'aereo', costo=Decimal('500'))
        self.v_yeezy = crear_variante(self.yeezy, 'aereo', costo=Decimal('700'))

    def sintetico(self):
        return (FIXTURES / 'cuadricula_sintetica.csv').read_bytes()

    def csv(self, *renglones):
        return (CABECERA + '\n'.join(renglones) + '\n').encode('utf-8')


class AnalisisTests(ImportarBase):
    def test_totales_y_repetidos_sumados(self):
        a = analizar_csv(self.sintetico(), via='aereo')
        self.assertTrue(a.ok, (a.errores, a.faltantes))
        self.assertEqual(a.total_pares, 12)
        self.assertEqual(a.total_proveedor, Decimal('3620'))
        self.assertEqual(len(a.renglones), 5)
        r = next(r for r in a.renglones
                 if r.variante == self.v_jordan and r.talla.mx_cm == Decimal('26'))
        self.assertEqual(r.cantidad, 3)
        self.assertEqual(r.color, self.negro)

    def test_bom_punto_y_coma_y_coma_decimal(self):
        base = analizar_csv(self.sintetico(), via='aereo')
        texto = self.sintetico().decode('utf-8')
        excel = ('﻿' + '\r\n'.join(
            ';'.join(linea.split(',')).replace('25.5', '25,5').replace('27.5', '27,5')
            for linea in texto.splitlines()) + '\r\n').encode('utf-8')
        self.assertIn(b'25,5', excel)
        a = analizar_csv(excel, via='aereo')
        self.assertEqual(a.errores, [])
        self.assertEqual(a.renglones, base.renglones)
        self.assertEqual(a.total_pares, base.total_pares)
        self.assertEqual(a.total_proveedor, base.total_proveedor)
        self.assertEqual(a.cambios_costo, base.cambios_costo)

    def test_encabezados_sin_importar_mayusculas_y_tarjeta_opcional(self):
        contenido = (
            'Modelo,COLOR,Talla_MX,Cantidad,Precio_Proveedor\n'
            'jordan 4,Negro,26,1,300\n').encode()
        a = analizar_csv(contenido, via='aereo')
        self.assertTrue(a.ok, (a.errores, a.faltantes))
        self.assertEqual(a.total_pares, 1)

    def test_color_por_alias(self):
        a = analizar_csv(self.csv('1,Jordan 4,Black,26,1,300'), via='aereo')
        self.assertTrue(a.ok, (a.errores, a.faltantes))
        self.assertEqual(a.renglones[0].color, self.negro)

    def test_cambios_costo_y_aviso_por_dos_precios(self):
        a = analizar_csv(self.sintetico(), via='aereo')
        cambios = {c.variante: c for c in a.cambios_costo}
        self.assertEqual(len(cambios), 3)
        j = cambios[self.v_jordan]
        self.assertEqual((j.costo_actual, j.costo_nuevo), (Decimal('640'), Decimal('600')))
        self.assertEqual(cambios[self.v_dunk].costo_nuevo, Decimal('530'))
        self.assertEqual(cambios[self.v_yeezy].costo_nuevo, Decimal('680'))
        self.assertEqual(len(a.avisos), 1)
        self.assertIn('Jordan 4', a.avisos[0])


class ErroresTests(ImportarBase):
    def assert_rechaza(self, contenido, fragmento):
        a = analizar_csv(contenido, via='aereo')
        self.assertFalse(a.ok)
        self.assertTrue(any(fragmento in e for e in a.errores), a.errores)
        with self.assertRaises(ValueError):
            confirmar_importacion(contenido, via='aereo', usuario=self.user)
        self.assertEqual(Lote.objects.count(), 0)
        return a

    def test_columna_faltante(self):
        self.assert_rechaza(b'modelo,color,talla_mx,cantidad\nJordan 4,negro,26,1\n',
                            'precio_proveedor')

    def test_talla_no_numerica_con_renglon(self):
        self.assert_rechaza(self.csv('1,Jordan 4,negro,26,1,300', '1,Jordan 4,negro,abc,1,300'),
                            'Renglón 3')

    def test_cantidad_cero(self):
        self.assert_rechaza(self.csv('1,Jordan 4,negro,26,0,300'), 'Renglón 2')

    def test_cantidad_no_entera(self):
        self.assert_rechaza(self.csv('1,Jordan 4,negro,26,3.0,300'), 'Renglón 2')

    def test_precio_negativo(self):
        self.assert_rechaza(self.csv('1,Jordan 4,negro,26,1,-1'), 'Renglón 2')

    def test_precio_no_finito(self):
        self.assert_rechaza(self.csv('1,Jordan 4,negro,26,1,NaN'), 'Renglón 2')

    def test_archivo_vacio(self):
        self.assert_rechaza(b'', 'vacío')
        self.assert_rechaza(CABECERA.encode(), 'vacío')

    def test_no_utf8(self):
        self.assert_rechaza(b'\xff\xfe\x00bad', 'UTF-8')


class FaltantesTests(ImportarBase):
    def test_inexistentes(self):
        a = analizar_csv(self.csv(
            '1,Modelo Fantasma,negro,26,1,300',
            '1,Jordan 4,rosa,26,1,300',
            '1,Jordan 4,negro,40,1,300',
            '1,Yeezy 350,negro,26,1,300',
        ), via='aereo')
        self.assertFalse(a.ok)
        self.assertEqual(a.errores, [])
        self.assertEqual(a.faltantes['modelos'], ['Modelo Fantasma'])
        self.assertEqual(a.faltantes['colores'], ['rosa'])
        self.assertEqual(a.faltantes['tallas'], ['40'])
        self.assertEqual(a.faltantes['colores_no_asignados'], ['Yeezy 350: negro'])

    def test_variante_faltante(self):
        a = analizar_csv(self.csv('1,Jordan 4,negro,26,1,300'), via='maritimo')
        self.assertFalse(a.ok)
        self.assertEqual(a.faltantes['variantes'], ['Jordan 4'])

    def test_variante_sin_prefijo_cuenta_como_faltante(self):
        TipoArticulo.objects.filter(pk=self.v_jordan.pk).update(prefijo=None)
        a = analizar_csv(self.csv('1,Jordan 4,negro,26,1,300'), via='aereo')
        self.assertEqual(a.faltantes['variantes'], ['Jordan 4'])

    def test_faltantes_sin_duplicados(self):
        a = analizar_csv(self.csv(
            '1,Fantasma,negro,26,1,300', '1,Fantasma,negro,27,1,300'), via='aereo')
        self.assertEqual(a.faltantes['modelos'], ['Fantasma'])


class ConfirmarTests(ImportarBase):
    def test_confirmar_crea_lote_pares_y_costo(self):
        lote = confirmar_importacion(
            self.sintetico(), via='aereo', usuario=self.user, referencia='Oct')
        self.assertEqual(lote.origen, 'csv')
        self.assertEqual(lote.referencia, 'Oct')
        self.assertEqual(lote.pares.count(), 12)
        self.assertEqual(lote.pares.filter(tipo=self.v_jordan).count(), 6)
        blanco = lote.pares.get(tipo=self.v_jordan, color=self.blanco)
        self.assertEqual(blanco.precio_proveedor, Decimal('320'))
        self.assertEqual(blanco.costo, Decimal('600'))
        negro = lote.pares.filter(tipo=self.v_jordan, color=self.negro).first()
        self.assertEqual(negro.precio_proveedor, Decimal('300'))
        self.assertEqual(negro.costo, Decimal('580'))
        self.v_jordan.refresh_from_db()
        self.v_dunk.refresh_from_db()
        self.assertEqual(self.v_jordan.costo, Decimal('600'))
        self.assertEqual(self.v_dunk.costo, Decimal('530'))

    def test_confirmar_con_envio_gratis(self):
        lote = confirmar_importacion(
            self.sintetico(), via='aereo', usuario=self.user, flete=Decimal('0'))
        self.assertEqual(lote.flete_por_par, Decimal('0'))
        blanco = lote.pares.get(tipo=self.v_jordan, color=self.blanco)
        self.assertEqual(blanco.costo, Decimal('320'))

    def test_confirmar_con_faltantes_no_crea_nada(self):
        with self.assertRaises(ValueError):
            confirmar_importacion(
                self.csv('1,Fantasma,negro,26,1,300'), via='aereo', usuario=self.user)
        self.assertEqual(Lote.objects.count(), 0)
        self.assertEqual(Par.objects.count(), 0)


class CuadriculaRealTests(TestCase):
    def setUp(self):
        self.negro = Color.objects.create(nombre='negro')
        for n in range(1, 34):
            base = _base(f'Tarjeta {n:02d}', f'tarjeta {n:02d}', self.negro)
            crear_variante(base, 'aereo', prefijo=f'T{n:02d}A', costo=Decimal('500'))

    def test_cuadricula_real_220_pares(self):
        self.assertEqual(TallaCalzado.objects.filter(activa=True).count(), 13)
        contenido = (FIXTURES / 'cuadricula_2026_10.csv').read_bytes()
        a = analizar_csv(contenido, via='aereo')
        self.assertTrue(a.ok, (a.errores, a.faltantes))
        self.assertEqual(a.total_pares, 220)
        self.assertEqual(a.total_proveedor, Decimal('72640'))


class MontosEnormesTests(ImportarBase):
    """M3: un precio que no cabe en el campo es error de renglón, no un DataError al escribir."""

    def renglon(self, precio):
        return self.csv(f'1,Jordan 4,negro,26,1,{precio}')

    def test_precio_enorme_es_error_de_renglon(self):
        a = analizar_csv(self.renglon('1E+12'), via='aereo')
        self.assertFalse(a.ok)
        self.assertTrue(any('Renglón 2' in e and 'precio' in e for e in a.errores), a.errores)

    def test_limite_exacto_cabe_y_el_siguiente_no(self):
        a = analizar_csv(self.renglon('999999.99'), via='aereo')
        self.assertFalse(any('precio' in e for e in a.errores), a.errores)
        a = analizar_csv(self.renglon('1000000'), via='aereo')
        self.assertTrue(any('Renglón 2' in e and 'precio' in e for e in a.errores), a.errores)

    def test_costo_con_flete_que_no_cabe_es_error(self):
        a = analizar_csv(self.renglon('999999.99'), via='aereo')
        self.assertFalse(a.ok)
        self.assertTrue(any('costo' in e for e in a.errores), a.errores)


class SeparadorMilesTests(ImportarBase):
    """M4: "1,250" no se adivina como 1.25."""

    def precio(self, texto):
        return analizar_csv(self.csv(f'1,Jordan 4,negro,26,1,"{texto}"'), via='aereo')

    def test_miles_con_coma_se_rechaza_con_mensaje(self):
        for texto in ('1,250', '12,500', '1,250,000', '1,250.50'):
            a = self.precio(texto)
            self.assertTrue(
                any('Renglón 2' in e and 'separador de miles' in e for e in a.errores),
                (texto, a.errores))

    def test_coma_decimal_corta_sigue_valida(self):
        a = self.precio('380,50')
        self.assertTrue(a.ok, a.errores)
        self.assertEqual(a.renglones[0].precio_proveedor, Decimal('380.50'))
        a = self.precio('26,5')
        self.assertEqual(a.renglones[0].precio_proveedor, Decimal('26.5'))

    def test_talla_26_5_con_coma_sigue_valida(self):
        a = analizar_csv(self.csv('1,Jordan 4,negro,"26,5",1,360'), via='aereo')
        self.assertFalse(any('talla' in e for e in a.errores), a.errores)
