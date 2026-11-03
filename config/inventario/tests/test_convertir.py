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

    def test_get_lista_solo_bases_sin_variante_aerea(self):
        self._staff()
        otro = _base('Nike', 'nike')
        crear_variante(otro, 'aereo')
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        nombres = [t.nombre for t in r.context['tipos']]
        self.assertEqual(nombres, ['Jordan 4'])

    def test_vista_convertir_post_crea_variantes(self):
        self._staff()
        r = self.client.post(self.url, {'tipo_ids': [self.base.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertIsNotNone(self.base.variante('aereo'))
        self.assertTrue(list(get_messages(r.wsgi_request)))
