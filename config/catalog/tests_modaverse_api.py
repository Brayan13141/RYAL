import httpx
from django.test import TestCase

from catalog.modaverse_api import ModaverseUnavailable, get_product


def _client(handler):
    """Cliente httpx con transporte falso — ningún test toca la red."""
    return httpx.Client(transport=httpx.MockTransport(handler))


class GetProductTests(TestCase):

    def test_producto_existente_devuelve_el_dict(self):
        def handler(request):
            return httpx.Response(200, json={
                'success': True,
                'data': {'productId': 'PR1', 'productName': 'FOG0124'},
            })
        with _client(handler) as c:
            self.assertEqual(get_product('PR1', client=c)['productName'], 'FOG0124')

    def test_manda_el_pid_en_el_body(self):
        visto = {}

        def handler(request):
            visto['body'] = request.read().decode()
            return httpx.Response(200, json={'success': True, 'data': {'productId': 'PR1'}})
        with _client(handler) as c:
            get_product('PR1', client=c)
        self.assertIn('PR1', visto['body'])

    def test_producto_inexistente_devuelve_none(self):
        def handler(request):
            return httpx.Response(200, json={'success': False, 'message': 'not found'})
        with _client(handler) as c:
            self.assertIsNone(get_product('NOPE', client=c))

    def test_data_nula_devuelve_none(self):
        def handler(request):
            return httpx.Response(200, json={'success': True, 'data': None})
        with _client(handler) as c:
            self.assertIsNone(get_product('NOPE', client=c))

    def test_401_es_api_cerrada(self):
        def handler(request):
            return httpx.Response(401, json={'message': 'unauthorized'})
        with _client(handler) as c:
            with self.assertRaises(ModaverseUnavailable):
                get_product('PR1', client=c)

    def test_403_es_api_cerrada(self):
        def handler(request):
            return httpx.Response(403, text='forbidden')
        with _client(handler) as c:
            with self.assertRaises(ModaverseUnavailable):
                get_product('PR1', client=c)

    def test_html_en_vez_de_json_es_api_cerrada(self):
        """Una pantalla de login o un challenge devuelven HTML, no JSON."""
        def handler(request):
            return httpx.Response(200, text='<!DOCTYPE html><html>login</html>')
        with _client(handler) as c:
            with self.assertRaises(ModaverseUnavailable):
                get_product('PR1', client=c)

    def test_success_false_con_mensaje_de_auth_es_api_cerrada(self):
        def handler(request):
            return httpx.Response(200, json={'success': False, 'message': 'token invalido'})
        with _client(handler) as c:
            with self.assertRaises(ModaverseUnavailable):
                get_product('PR1', client=c)

    def test_error_de_red_reintenta_una_vez_y_despues_falla(self):
        intentos = []

        def handler(request):
            intentos.append(1)
            raise httpx.ConnectError('sin conexion')
        with _client(handler) as c:
            with self.assertRaises(ModaverseUnavailable):
                get_product('PR1', client=c)
        self.assertEqual(len(intentos), 2)

    def test_error_de_red_transitorio_se_recupera_en_el_reintento(self):
        intentos = []

        def handler(request):
            intentos.append(1)
            if len(intentos) == 1:
                raise httpx.ConnectError('sin conexion')
            return httpx.Response(200, json={'success': True, 'data': {'productId': 'PR1'}})
        with _client(handler) as c:
            self.assertEqual(get_product('PR1', client=c)['productId'], 'PR1')

    def test_500_reintenta_y_despues_falla(self):
        intentos = []

        def handler(request):
            intentos.append(1)
            return httpx.Response(500, text='boom')
        with _client(handler) as c:
            with self.assertRaises(ModaverseUnavailable):
                get_product('PR1', client=c)
        self.assertEqual(len(intentos), 2)
