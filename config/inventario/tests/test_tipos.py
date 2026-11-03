from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from catalog.models import TipoArticulo
from inventario.models import Color
from negocio.services import resolver_tipo


def _tipo(nombre, keywords, **kw):
    return TipoArticulo(nombre=nombre, keywords=keywords, costo=Decimal('100'), **kw)


class TipoArticuloVariantesTests(TestCase):
    def setUp(self):
        self.base = _tipo('Jordan 4', 'jordan 4')
        self.base.full_clean()
        self.base.save()

    def _variante(self, via='aereo', keywords='jordan 4 aereo', nombre='Jordan 4 A', **kw):
        kw.setdefault('usa_tallas', True)
        return _tipo(nombre, keywords, padre=self.base, via=via, **kw)

    def assertErrorEn(self, tipo, campo):
        with self.assertRaises(ValidationError) as cm:
            tipo.full_clean()
        self.assertIn(campo, cm.exception.message_dict)

    def test_variante_requiere_padre_base(self):
        self.assertErrorEn(_tipo('X', 'x aereo', via='aereo'), 'padre')

    def test_padre_no_puede_ser_variante(self):
        var = self._variante()
        var.full_clean()
        var.save()
        nieta = _tipo('Nieta', 'nieta maritimo', padre=var, via='maritimo')
        self.assertErrorEn(nieta, 'padre')

    def test_una_variante_por_via_por_padre(self):
        v1 = self._variante()
        v1.full_clean()
        v1.save()
        v2 = self._variante(nombre='Jordan 4 A2', keywords='jordan 4 x aereo')
        self.assertErrorEn(v2, 'via')

    def test_prefijo_formato(self):
        t = _tipo('P', 'p', prefijo='jd')
        t.full_clean()
        t.save()
        self.assertEqual(TipoArticulo.objects.get(pk=t.pk).prefijo, 'JD')
        for malo in ('J', 'JD4AXX', 'JD-4'):
            self.assertErrorEn(_tipo('Q', 'q', prefijo=malo), 'prefijo')

    def test_keyword_de_variante_debe_terminar_en_via(self):
        self.assertErrorEn(self._variante(keywords='jordan 4 a'), 'keywords')
        self.assertErrorEn(
            self._variante(via='maritimo', nombre='J4M', keywords='jordan 4 mar'),
            'keywords')
        self._variante(keywords='jordan 4 aereo, jordan 4 aéreo').full_clean()

    def test_helpers(self):
        var_a = self._variante()
        var_a.full_clean()
        var_a.save()
        negro = Color.objects.create(nombre='ZZ Negro Test')
        self.base.colores.add(negro)
        self.assertTrue(self.base.tiene_variantes)
        self.assertFalse(var_a.tiene_variantes)
        self.assertEqual(self.base.variante('aereo'), var_a)
        self.assertIsNone(self.base.variante('maritimo'))
        self.assertTrue(var_a.es_variante)
        self.assertFalse(self.base.es_variante)
        self.assertEqual(var_a.modelo_base, self.base)
        self.assertEqual(self.base.modelo_base, self.base)
        self.assertEqual(list(var_a.colores_efectivos()), [negro])

    def test_resolver_tipo_variante_gana_al_base(self):
        var_a = self._variante()
        var_a.full_clean()
        var_a.save()
        tipos = list(TipoArticulo.objects.all())
        self.assertEqual(resolver_tipo('jordan 4 aereo negro t27', tipos, {}), var_a)
        self.assertEqual(resolver_tipo('jordan 4 negro t27', tipos, {}), self.base)
