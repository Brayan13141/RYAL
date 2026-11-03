from django.test import SimpleTestCase

from catalog.models import ProductImage, ProductVariant
from orders.variantes import nombre_variante


class NombreVarianteTests(SimpleTestCase):
    def setUp(self):
        # Objetos en memoria: la función solo lee atributos.
        self.img_blanco = ProductImage(color_label='Blanco', display_order=0)
        self.img_sin_label = ProductImage(color_label='', display_order=1)
        self.variant = ProductVariant(name='Edición limitada')

    def test_talla_y_color_texto(self):
        self.assertEqual(nombre_variante('L', 'Rojo', None, None, None), 'Talla L / Rojo')

    def test_talla_y_colorway_con_label(self):
        self.assertEqual(nombre_variante('26', None, self.img_blanco, None, None), 'Blanco · Talla 26')

    def test_talla_y_colorway_sin_label_usa_color_num(self):
        self.assertEqual(nombre_variante('26', None, self.img_sin_label, 3, None), 'Color 3 · Talla 26')

    def test_talla_y_colorway_sin_label_ni_num_usa_display_order(self):
        self.assertEqual(nombre_variante('26', None, self.img_sin_label, None, None), 'Color 2 · Talla 26')

    def test_solo_color(self):
        self.assertEqual(nombre_variante(None, 'Negro', None, None, None), 'Negro')

    def test_solo_talla(self):
        self.assertEqual(nombre_variante('M', None, None, None, None), 'Talla M')

    def test_solo_colorway(self):
        self.assertEqual(nombre_variante(None, None, self.img_blanco, None, None), 'Blanco')

    def test_solo_colorway_sin_label_usa_color_num_y_display_order(self):
        self.assertEqual(nombre_variante(None, None, self.img_sin_label, 3, None), 'Color 3')
        self.assertEqual(nombre_variante(None, None, self.img_sin_label, None, None), 'Color 2')

    def test_product_variant(self):
        self.assertEqual(nombre_variante(None, None, None, None, self.variant), self.variant.name)

    def test_nada(self):
        self.assertEqual(nombre_variante(None, None, None, None, None), '')

    def test_parse_variant_lee_lo_que_escribe(self):
        from orders.cart_builder import parse_variant
        self.assertEqual(parse_variant(nombre_variante('26', None, self.img_blanco, None, None)), ('26', 'Blanco'))
        self.assertEqual(parse_variant(nombre_variante('L', 'Rojo', None, None, None)), ('L', 'Rojo'))
