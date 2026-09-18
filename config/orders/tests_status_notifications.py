import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.models import Order

URL_17TRACK = 'https://t.17track.net/en#nums=JMX123456789'


def _order(status='confirmed', **kw):
    datos = dict(
        order_code='ST-1', customer_name='Ana López', customer_phone='5512345678', status=status,
    )
    datos.update(kw)
    return Order.objects.create(**datos)


class BuildStatusMessageTests(TestCase):
    def test_confirmado_lleva_link_de_seguimiento(self):
        from orders.notifications import build_status_message
        order = _order()
        link = settings.SITE_URL + reverse('orders:confirmation', args=[order.tracking_token])
        self.assertEqual(
            build_status_message(order),
            f'✅ Hola Ana, tu pedido #ST-1 fue confirmado. Síguelo aquí: {link}',
        )

    def test_en_preparacion(self):
        from orders.notifications import build_status_message
        message = build_status_message(_order(status='in_preparation'))
        self.assertTrue(message.startswith('📦 Hola Ana, tu pedido #ST-1 ya se está preparando.'))

    def test_enviado_lleva_la_url_del_proveedor_no_la_del_sitio(self):
        from orders.notifications import build_status_message
        message = build_status_message(_order(status='shipped', tracking_url=URL_17TRACK))
        self.assertEqual(message, f'🚚 Hola Ana, tu pedido #ST-1 ya va en camino. Rastréalo aquí: {URL_17TRACK}')
        self.assertNotIn(settings.SITE_URL, message)


class NotifyStatusChangeAsyncTests(TestCase):
    @patch('orders.notifications.threading.Thread')
    def test_lanza_thread_daemon_con_el_mensaje_ya_armado(self, mock_thread_cls):
        from orders.notifications import _post_notify_customer, build_status_message, notify_status_change_async
        order = _order(customer_phone='+52 (55) 1234-5678')
        notify_status_change_async(order)
        mock_thread_cls.assert_called_once()
        _, kwargs = mock_thread_cls.call_args
        self.assertIs(kwargs['target'], _post_notify_customer)
        self.assertTrue(kwargs['daemon'])
        self.assertEqual(kwargs['args'], ('5512345678', build_status_message(order), 'ST-1'))
        mock_thread_cls.return_value.start.assert_called_once()

    @patch('orders.notifications.threading.Thread')
    def test_estados_que_no_avisan(self, mock_thread_cls):
        from orders.notifications import notify_status_change_async
        for status in ('pending', 'cancelled', 'delivered'):
            with self.subTest(status=status):
                notify_status_change_async(_order(status=status, order_code=f'ST-{status}'))
        mock_thread_cls.assert_not_called()

    @patch('orders.notifications.threading.Thread')
    def test_telefono_invalido_no_avisa(self, mock_thread_cls):
        from orders.notifications import notify_status_change_async
        notify_status_change_async(_order(customer_phone='12345'))
        mock_thread_cls.assert_not_called()

    @patch('orders.notifications.threading.Thread', side_effect=RuntimeError("can't start new thread"))
    def test_no_propaga_si_el_thread_no_arranca(self, mock_thread_cls):
        from orders.notifications import notify_status_change_async
        notify_status_change_async(_order())  # no debe lanzar


class PostNotifyCustomerTests(TestCase):
    @override_settings(NOTIFY_TOKEN='tok-123')
    @patch('orders.notifications.urllib.request.urlopen')
    def test_manda_el_whatsapp_con_token(self, mock_urlopen):
        from orders.notifications import _post_notify_customer
        _post_notify_customer('5512345678', 'hola Ana', 'ST-1')
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, f'{settings.BOT_NOTIFY_URL}/notify')
        self.assertEqual(req.get_header('X-notify-token'), 'tok-123')
        self.assertEqual(
            json.loads(req.data.decode('utf-8')),
            {'message': 'hola Ana', 'target': 'customer', 'phone': '5512345678'},
        )

    @override_settings(NOTIFY_TOKEN='')
    @patch('orders.notifications.urllib.request.urlopen')
    def test_sin_token_no_hay_post(self, mock_urlopen):
        from orders.notifications import _post_notify_customer
        _post_notify_customer('5512345678', 'hola Ana', 'ST-1')
        mock_urlopen.assert_not_called()

    @override_settings(NOTIFY_TOKEN='tok-123')
    @patch('orders.notifications.urllib.request.urlopen', side_effect=OSError('conexión rechazada'))
    def test_bot_caido_no_propaga(self, mock_urlopen):
        from orders.notifications import _post_notify_customer
        _post_notify_customer('5512345678', 'hola Ana', 'ST-1')  # no debe lanzar
        mock_urlopen.assert_called_once()
