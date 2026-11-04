from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from catalog.models import TipoArticulo
from inventario.services.tipos import _candidatos, crear_variante, sugerir_prefijo
from negocio.models import Pedido, PedidoItem


def _base(nombre='Jordan 4', keywords='jordan 4, j4', costo='540', **kw):
    t = TipoArticulo(nombre=nombre, keywords=keywords, costo=Decimal(costo), **kw)
    t.full_clean()
    t.save()
    return t


class SugerirPrefijoTests(TestCase):
    def test_iniciales_y_digitos(self):
        self.assertEqual(sugerir_prefijo('Jordan 4', 'aereo'), 'JD4A')
        self.assertEqual(sugerir_prefijo('Jordan 4', 'maritimo'), 'JD4M')

    def test_choque_cambia_tercer_caracter(self):
        _base('Otro', 'otro', prefijo='JD4A')
        self.assertEqual(sugerir_prefijo('Jordan 4', 'aereo'), 'JD2A')

    def test_nombre_corto_da_al_menos_dos_caracteres(self):
        self.assertGreaterEqual(len(sugerir_prefijo('Nike', 'aereo')), 3)
        self.assertEqual(len(sugerir_prefijo('X', 'aereo')) >= 2, True)

    def test_todos_ocupados_levanta(self):
        for i, c in enumerate(_candidatos('Jordan 4', 'aereo')):
            _base(f'T{i}', f't{i}', prefijo=c)
        with self.assertRaises(ValueError):
            sugerir_prefijo('Jordan 4', 'aereo')

    def test_cuarenta_nombres_parecidos_dan_prefijos_distintos(self):
        prefijos = set()
        for n in range(40):
            nombre = f'Nike Air {n:02d}'
            base = _base(nombre, nombre.lower())
            v = crear_variante(base, 'aereo')
            prefijos.add(v.prefijo)
        self.assertEqual(len(prefijos), 40)


class CrearVarianteTests(TestCase):
    def setUp(self):
        self.base = _base()

    def test_crear_variante_copia_costo_y_arma_keywords(self):
        v = crear_variante(self.base, 'aereo')
        self.assertEqual(v.nombre, 'Jordan 4 A')
        self.assertEqual(v.costo, Decimal('540'))
        self.assertEqual(v.keywords_list, [
            'jordan 4 aereo', 'jordan 4 aéreo', 'j4 aereo', 'j4 aéreo'])
        self.assertEqual(v.padre, self.base)
        self.assertEqual(v.via, 'aereo')
        self.assertEqual(v.prefijo, 'JD4A')
        self.assertTrue(v.usa_tallas)
        self.base.refresh_from_db()
        self.assertTrue(self.base.usa_tallas)

    def test_costo_dado_y_maritimo(self):
        v = crear_variante(self.base, 'maritimo', costo=Decimal('500'))
        self.assertEqual(v.nombre, 'Jordan 4 M')
        self.assertEqual(v.costo, Decimal('500'))
        self.assertIn('j4 marítimo', v.keywords_list)

    def test_crear_variante_idempotente(self):
        v1 = crear_variante(self.base, 'aereo')
        v2 = crear_variante(self.base, 'aereo', costo=Decimal('1'))
        self.assertEqual(v1.pk, v2.pk)
        self.assertEqual(v2.costo, Decimal('540'))
        self.assertEqual(TipoArticulo.objects.filter(padre=self.base).count(), 1)

    def test_no_toca_ventas_historicas(self):
        pedido = Pedido.objects.create(
            descripcion='jordan 4', costo_producto=Decimal('540'),
            precio_venta=Decimal('900'))
        item = PedidoItem.objects.create(
            pedido=pedido, sku_snapshot='X', nombre_snapshot='jordan 4',
            cantidad=1, costo_unitario=Decimal('540'),
            precio_unitario=Decimal('900'))
        crear_variante(self.base, 'aereo')
        item.refresh_from_db()
        self.assertEqual(item.costo_unitario, Decimal('540'))
        self.assertEqual(item.nombre_snapshot, 'jordan 4')


class VistaConvertirTests(TestCase):
    def setUp(self):
        self.url = reverse('inventario:tipos_convertir')
        self.base = _base()

    def _staff(self):
        User.objects.create_user('staff', password='pass', is_staff=True)
        self.client.login(username='staff', password='pass')

    def test_vista_convertir_solo_staff(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r['Location'])

    def test_get_un_renglon_por_base_con_sus_tres_vias(self):
        self._staff()
        otro = _base('Nike', 'nike')
        v = crear_variante(otro, 'maritimo', costo=Decimal('400'))
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        filas = {f['base'].nombre: {via: v for via, v, _ in f['celdas']} for f in r.context['filas']}
        self.assertEqual(sorted(filas), ['Jordan 4', 'Nike'])
        self.assertEqual(filas['Nike'], {'aereo': None, 'maritimo': v, 'nacional': None})
        self.assertNotIn('Nike M', filas)  # las variantes no son renglón
        html = r.content.decode()
        self.assertIn(f'value="{self.base.pk}:nacional"', html)
        self.assertNotIn(f'value="{otro.pk}:maritimo"', html)

    def test_vista_convertir_post_crea_variantes(self):
        self._staff()
        r = self.client.post(self.url, {'crear': [f'{self.base.pk}:aereo']})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.base.variante('aereo').costo, Decimal('540'))
        self.assertTrue(list(get_messages(r.wsgi_request)))

    def test_post_crea_maritima_y_nacional_con_costo_capturado(self):
        self._staff()
        self.client.post(self.url, {
            'crear': [f'{self.base.pk}:maritimo', f'{self.base.pk}:nacional'],
            f'costo-{self.base.pk}-maritimo': '490',
            f'costo-{self.base.pk}-nacional': '345,50'})
        self.assertEqual(self.base.variante('maritimo').costo, Decimal('490.00'))
        n = self.base.variante('nacional')
        self.assertEqual(n.costo, Decimal('345.50'))
        self.assertEqual(n.nombre, 'Jordan 4 N')

    def test_post_costo_invalido_no_crea_esa_variante(self):
        self._staff()
        r = self.client.post(self.url, {
            'crear': [f'{self.base.pk}:nacional', f'{self.base.pk}:aereo'],
            f'costo-{self.base.pk}-nacional': 'abc'})
        self.assertIsNone(self.base.variante('nacional'))
        self.assertIsNotNone(self.base.variante('aereo'))
        textos = [str(m) for m in get_messages(r.wsgi_request)]
        self.assertTrue(any('nacional' in t and 'abc' in t for t in textos), textos)

    def test_post_ignora_via_o_base_invalidas(self):
        self._staff()
        v = crear_variante(self.base, 'aereo')
        self.client.post(self.url, {'crear': [f'{self.base.pk}:terrestre', f'{v.pk}:nacional', 'x:aereo']})
        self.assertEqual(TipoArticulo.objects.count(), 2)

    def test_post_sin_seleccion_avisa(self):
        self._staff()
        r = self.client.post(self.url, {})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(TipoArticulo.objects.count(), 1)

    def test_costo_sugerido_es_proveedor_mas_flete_de_cada_via(self):
        # Tarifas sembradas: aéreo 280, marítimo 180, nacional 50. Jordan 4 cuesta 540
        # (= 260 de proveedor + 280 aéreo).
        self._staff()
        r = self.client.get(self.url)
        fila = next(f for f in r.context['filas'] if f['base'] == self.base)
        self.assertEqual(fila['proveedor'], Decimal('260.00'))
        self.assertEqual({via: c for via, _, c in fila['celdas']},
                         {'aereo': Decimal('540.00'), 'maritimo': Decimal('440.00'),
                          'nacional': Decimal('310.00')})

    def test_post_sin_costo_usa_proveedor_mas_flete(self):
        self._staff()
        self.client.post(self.url, {'crear': [f'{self.base.pk}:maritimo', f'{self.base.pk}:nacional']})
        self.assertEqual(self.base.variante('maritimo').costo, Decimal('440.00'))
        self.assertEqual(self.base.variante('nacional').costo, Decimal('310.00'))

    def test_post_proveedor_capturado_cambia_la_cuenta(self):
        self._staff()
        self.client.post(self.url, {'crear': [f'{self.base.pk}:nacional'],
                                    f'proveedor-{self.base.pk}': '300'})
        self.assertEqual(self.base.variante('nacional').costo, Decimal('350.00'))

    def test_proveedor_nunca_negativo(self):
        self._staff()
        barato = _base('Moda', 'moda', costo='0')
        r = self.client.get(self.url)
        fila = next(f for f in r.context['filas'] if f['base'] == barato)
        self.assertEqual(fila['proveedor'], Decimal('0.00'))
