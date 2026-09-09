from django.test import TestCase


class RateLimitKeyBehindUnixSocketTests(TestCase):
    """Nginx habla con Gunicorn por socket Unix, así que REMOTE_ADDR llega vacío.

    Los decoradores @ratelimit(key='ip') de esta app revientan en ese escenario y
    la vista devuelve 500 — en producción los tres endpoints con key='ip' están
    caídos mientras los que no tienen ratelimit responden 200.
    """

    def test_track_no_revienta_sin_remote_addr(self):
        resp = self.client.get('/api/orders/track/', {'code': 'RY0000000001'}, REMOTE_ADDR='')
        self.assertNotEqual(resp.status_code, 500)

    def test_login_no_revienta_sin_remote_addr(self):
        resp = self.client.post('/api/auth/login/', {}, REMOTE_ADDR='')
        self.assertNotEqual(resp.status_code, 500)

    def test_cart_add_no_revienta_sin_remote_addr(self):
        resp = self.client.post('/api/cart/add/', {}, REMOTE_ADDR='')
        self.assertNotEqual(resp.status_code, 500)


class ClientIpKeyTests(TestCase):
    """La clave de rate limit tiene que ser la IP que puso el proxy, no algo que
    el cliente controle. Nginx AGREGA la IP real al final de X-Forwarded-For, así
    que el último valor es el único de fiar: el prefijo lo escribe quien llama.
    """

    def test_rotar_el_prefijo_de_xff_no_evade_el_limite(self):
        # order_track admite 20/m y responde 429 explícito — sin token de por
        # medio, así que el bloqueo es observable y no se confunde con un 403.
        bloqueada = False
        for i in range(30):
            resp = self.client.get(
                '/api/orders/track/',
                {'code': 'RY0000000001'},
                HTTP_X_FORWARDED_FOR=f'10.0.0.{i}, 203.0.113.7',
            )
            if resp.status_code == 429:
                bloqueada = True
                break
        self.assertTrue(
            bloqueada,
            'El límite nunca se disparó: rotando el prefijo de X-Forwarded-For se evade.',
        )


class ApiRastreoTests(TestCase):
    """El endpoint público de rastreo devolvía el OrderSerializer completo, que
    incluye `customer_phone` y `tracking_token`. Entregar el token convierte una
    lectura en acceso permanente: es la capability URL de la confirmación.
    """

    def setUp(self):
        from orders.models import Order
        self.order = Order.objects.create(
            order_code='RY2609090001', customer_name='Ana Torres',
            customer_phone='5512345678',
        )

    def _track(self, **params):
        return self.client.get('/api/orders/track/', params)

    def test_sin_telefono_no_devuelve_el_pedido(self):
        resp = self._track(code='RY2609090001')
        self.assertNotContains(resp, 'Ana Torres', status_code=resp.status_code)

    def test_con_telefono_incorrecto_no_devuelve_el_pedido(self):
        resp = self._track(code='RY2609090001', phone='5599999999')
        self.assertNotContains(resp, 'Ana Torres', status_code=resp.status_code)

    def test_con_telefono_correcto_devuelve_el_pedido(self):
        resp = self._track(code='RY2609090001', phone='5512345678')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['customer_name'], 'Ana Torres')

    def test_no_expone_el_telefono_ni_el_tracking_token(self):
        resp = self._track(code='RY2609090001', phone='5512345678')
        data = resp.json()
        self.assertNotIn('customer_phone', data)
        self.assertNotIn('tracking_token', data)

    def test_no_distingue_codigo_inexistente_de_telefono_erroneo(self):
        inexistente = self._track(code='RY9999999999', phone='5512345678')
        mal_tel = self._track(code='RY2609090001', phone='5599999999')
        self.assertEqual(inexistente.status_code, mal_tel.status_code)
        self.assertEqual(inexistente.json(), mal_tel.json())
