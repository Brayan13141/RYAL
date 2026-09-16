from django.test import TestCase

from orders.cart_builder import (
    build_cart_entry, build_cart_script, parse_variant,
    spec_dimension, spec_label, stock_warnings,
)


def _spec(sid, dim_zh, dim_es, visible, valor, precio=0.0):
    return {
        'productSpecificationsId': sid,
        'specificationsName':      dim_zh,
        'foreignLanguageName1':    dim_es,
        'foreignLanguageName2':    visible,
        'productId':               'PR20260325154032003456',
        'specificationsValue':     valor,
        'unitPrice':               precio,
    }


# Producto real capturado de la API el 2026-09-15.
FOG0124 = {
    'productId':      'PR20260325154032003456',
    'productName':    'FOG0124',
    'specifications': '尺寸:S:0:talla:S,尺寸:M:0:talla:M,尺寸:L:0:talla:L,尺寸:XL:0:talla:XL,',
    'ynLaunch':       '1',
    'unitPrice':      250,
    'stockNum':       80,
    'categoryId':     'CA20260107160742000002',
    'productSpecificationsList': [
        _spec('2072127247497560065', '尺寸', 'talla', 'S',     'S'),
        _spec('2072127247497560066', '尺寸', 'talla', 'M',     'M'),
        _spec('2072127247497560067', '尺寸', 'talla', 'L',     'L'),
        _spec('2072127247497560068', '尺寸', 'talla', 'XL',    'XL'),
        _spec('2072127247497560069', '颜色', 'Color', 'Beige', '米黄'),
        _spec('2072127247497560070', '颜色', 'Color', 'Negro', '黑'),
        _spec('2072127247497560071', '颜色', 'Color', 'Gris',  '灰色'),
    ],
}

# Producto sin especificaciones (los que el código viejo agregaba con btn_1).
ZA156 = {
    'productId':                 '2062727000623153154',
    'productName':               'ZA-156',
    'specifications':            None,
    'ynLaunch':                  '0',
    'unitPrice':                 250,
    'stockNum':                  0,
    'categoryId':                'CA20260107160742000002',
    'productSpecificationsList': [],
}


class ParseVariantTests(TestCase):

    def test_talla_y_color(self):
        self.assertEqual(parse_variant('Talla L / Rojo burdeos'), ('L', 'Rojo burdeos'))

    def test_solo_talla(self):
        self.assertEqual(parse_variant('Talla L'), ('L', ''))

    def test_solo_color(self):
        self.assertEqual(parse_variant('Rojo burdeos'), ('', 'Rojo burdeos'))

    def test_vacio(self):
        self.assertEqual(parse_variant(''), ('', ''))

    def test_colorway_de_calzado_lleva_el_color_primero(self):
        """orders/views.py arma "{color} · Talla {size}" en el modo colorway."""
        self.assertEqual(parse_variant('Blanco · Talla 26'), ('26', 'Blanco'))


class SpecDimensionTests(TestCase):

    def test_talla_en_minuscula(self):
        self.assertEqual(spec_dimension(FOG0124['productSpecificationsList'][0]), 'size')

    def test_color_capitalizado(self):
        """foreignLanguageName1 viene como 'Color' con mayúscula."""
        self.assertEqual(spec_dimension(FOG0124['productSpecificationsList'][4]), 'color')

    def test_dimension_desconocida(self):
        self.assertEqual(spec_dimension(_spec('x', '材质', 'material', 'Algodón', '棉')), '')

    def test_label_prefiere_el_español(self):
        self.assertEqual(spec_label(FOG0124['productSpecificationsList'][4]), 'Beige')


class BuildCartEntryTests(TestCase):

    def test_producto_sin_specs(self):
        entry, status, notas = build_cart_entry(ZA156, '', 3)
        self.assertEqual(status, 'added')
        self.assertEqual(entry['orderSpecificationsList'], [])
        self.assertEqual(entry['guige'], [])
        self.assertEqual(entry['num'], 3)

    def test_producto_con_talla(self):
        entry, status, notas = build_cart_entry(FOG0124, 'Talla M', 2)
        self.assertEqual(status, 'added')
        self.assertEqual([s['foreignLanguageName2'] for s in entry['orderSpecificationsList']], ['M'])
        self.assertEqual(entry['num'], 2)

    def test_producto_con_talla_y_color(self):
        entry, status, notas = build_cart_entry(FOG0124, 'Talla L / Negro', 1)
        self.assertEqual(status, 'added')
        elegidos = [s['foreignLanguageName2'] for s in entry['orderSpecificationsList']]
        self.assertEqual(sorted(elegidos), ['L', 'Negro'])

    def test_producto_con_colorway_de_calzado(self):
        entry, status, _ = build_cart_entry(FOG0124, 'Negro · Talla L', 1)
        self.assertEqual(status, 'added')
        elegidos = [s['foreignLanguageName2'] for s in entry['orderSpecificationsList']]
        self.assertEqual(sorted(elegidos), ['L', 'Negro'])

    def test_dimension_no_pedida_se_agrega_sin_elegir_y_avisa(self):
        """Pedido con talla y producto con color: no se inventa el color, se avisa."""
        entry, status, notas = build_cart_entry(FOG0124, 'Talla M', 1)
        self.assertEqual(status, 'added')
        elegidos = [s['foreignLanguageName2'] for s in entry['orderSpecificationsList']]
        self.assertEqual(elegidos, ['M'])
        self.assertIn('"Color" sin elegir', notas)

    def test_no_muta_el_producto_de_entrada(self):
        """build_cart_entry no puede ensuciar el dict que le pasaron."""
        antes = dict(ZA156)
        build_cart_entry(ZA156, '', 1)
        self.assertEqual(ZA156, antes)
        self.assertNotIn('num', ZA156)

    def test_guige_marca_isSelect_solo_en_la_elegida(self):
        entry, _, _ = build_cart_entry(FOG0124, 'Talla L / Negro', 1)
        tallas = [g for g in entry['guige'] if g['foreignLanguageName1'] == 'talla'][0]
        seleccionadas = [z['foreignLanguageName2'] for z in tallas['zhiList'] if z['isSelect']]
        self.assertEqual(seleccionadas, ['L'])
        self.assertEqual(len(tallas['zhiList']), 4)

    def test_guige_agrupa_las_dos_dimensiones(self):
        entry, _, _ = build_cart_entry(FOG0124, 'Talla L / Negro', 1)
        self.assertEqual([g['foreignLanguageName1'] for g in entry['guige']], ['talla', 'Color'])

    def test_variante_inexistente_no_entra_al_carrito(self):
        entry, status, notas = build_cart_entry(FOG0124, 'Talla XXXL', 1)
        self.assertIsNone(entry)
        self.assertEqual(status, 'variant_not_found')
        self.assertIn('XXXL', notas)
        self.assertIn('XL', notas)   # la nota lista las opciones reales

    def test_color_inexistente_no_entra_al_carrito(self):
        entry, status, notas = build_cart_entry(FOG0124, 'Talla L / Fucsia', 1)
        self.assertIsNone(entry)
        self.assertEqual(status, 'variant_not_found')

    def test_matchea_sin_distinguir_mayusculas(self):
        entry, status, _ = build_cart_entry(FOG0124, 'Talla L / negro', 1)
        self.assertEqual(status, 'added')

    def test_matchea_por_el_valor_chino_como_respaldo(self):
        entry, status, _ = build_cart_entry(FOG0124, '黑', 1)
        self.assertEqual(status, 'added')

    def test_producto_con_specs_pero_sin_variante_toma_la_primera_y_avisa(self):
        """Conserva el comportamiento del código viejo (_add_with_specs_multi)."""
        entry, status, notas = build_cart_entry(FOG0124, '', 1)
        self.assertEqual(status, 'added')
        elegidos = [s['foreignLanguageName2'] for s in entry['orderSpecificationsList']]
        self.assertEqual(sorted(elegidos), ['Beige', 'S'])
        self.assertIn('Sin variante', notas)

    def test_category_name_va_en_los_dos_campos(self):
        entry, _, _ = build_cart_entry(ZA156, '', 1, category_name='Dandy y Barbas')
        self.assertEqual(entry['categoryName'], 'Dandy y Barbas')
        self.assertEqual(entry['foreignLanguageName'], 'Dandy y Barbas')

    def test_conserva_los_campos_del_producto(self):
        entry, _, _ = build_cart_entry(FOG0124, 'Talla M', 1)
        self.assertEqual(entry['unitPrice'], 250)
        self.assertEqual(entry['productName'], 'FOG0124')
        self.assertEqual(entry['specifications'], FOG0124['specifications'])


class StockWarningsTests(TestCase):

    def test_despublicado(self):
        self.assertIn('despublicado', ' '.join(stock_warnings(ZA156, 1)))

    def test_stock_insuficiente(self):
        self.assertIn('stock', ' '.join(stock_warnings(FOG0124, 999)))

    def test_stock_suficiente_y_publicado_no_avisa(self):
        self.assertEqual(stock_warnings(FOG0124, 5), [])


class BuildCartScriptTests(TestCase):

    def test_script_lleva_los_items_y_recarga(self):
        entry, _, _ = build_cart_entry(FOG0124, 'Talla M', 2)
        script = build_cart_script([entry])
        self.assertIn('shopCarList', script)
        self.assertIn('FOG0124', script)
        self.assertIn('location.reload()', script)

    def test_script_preserva_el_user_existente(self):
        """Fusiona en el user de localStorage para no perder el userToken."""
        entry, _, _ = build_cart_entry(ZA156, '', 1)
        script = build_cart_script([entry])
        self.assertIn("localStorage.getItem('user')", script)

    def test_sin_entradas_devuelve_cadena_vacia(self):
        self.assertEqual(build_cart_script([]), '')
