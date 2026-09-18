import json
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.models import Order

URL_17TRACK = 'https://t.17track.net/en#nums=JMX123456789'
CORREO_OK = dict(
    NOTIFY_TOKEN='tok-123', EMAIL_HOST_USER='ryal@gmail.com',
    EMAIL_HOST_PASSWORD='app-pass', DEFAULT_FROM_EMAIL='ryal@gmail.com',
)


def _order(status='confirmed', **kw):
    datos = dict(
        order_code='ST-1', customer_name='Ana López', customer_phone='5512345678',
        customer_email='ana@example.com', status=status,
    )
    datos.update(kw)
    return Order.objects.create(**datos)


class BuildStatusNoticeTests(TestCase):
    def test_confirmado_lleva_link_de_seguimiento(self):
        from orders.notifications import build_status_notice
        order = _order()
        subject, message = build_status_notice(order)
        link = settings.SITE_URL + reverse('orders:confirmation', args=[order.tracking_token])
        self.assertEqual(message, f'✅ Hola Ana, tu pedido #ST-1 fue confirmado. Síguelo aquí: {link}')
        self.assertEqual(subject, 'Tu pedido #ST-1 — Confirmado')

    def test_en_preparacion(self):
        from orders.notifications import build_status_notice
        order = _order(status='in_preparation')
        _, message = build_status_notice(order)
        self.assertTrue(message.startswith('📦 Hola Ana, tu pedido #ST-1 ya se está preparando.'))

    def test_enviado_lleva_la_url_del_proveedor_no_la_del_sitio(self):
        from orders.notifications import build_status_notice
        order = _order(status='shipped', tracking_url=URL_17TRACK)
        subject, message = build_status_notice(order)
        self.assertEqual(message, f'🚚 Hola Ana, tu pedido #ST-1 ya va en camino. Rastréalo aquí: {URL_17TRACK}')
        self.assertNotIn(settings.SITE_URL, message)
        self.assertEqual(subject, 'Tu pedido #ST-1 — Enviado')


class NotifyStatusChangeAsyncTests(TestCase):
    @patch('orders.notifications.threading.Thread')
    def test_lanza_thread_daemon_con_todo_ya_armado(self, mock_thread_cls):
        from orders.notifications import _send_status_notice, build_status_notice, notify_status_change_async
        order = _order(customer_phone='+52 (55) 1234-5678')
        notify_status_change_async(order)
        mock_thread_cls.assert_called_once()
        _, kwargs = mock_thread_cls.call_args
        self.assertIs(kwargs['target'], _send_status_notice)
        self.assertTrue(kwargs['daemon'])
        subject, message = build_status_notice(order)
        self.assertEqual(kwargs['args'], ('5512345678', 'ana@example.com', subject, message, 'ST-1'))
        mock_thread_cls.return_value.start.assert_called_once()

    @patch('orders.notifications.threading.Thread')
    def test_estados_que_no_avisan(self, mock_thread_cls):
        from orders.notifications import notify_status_change_async
        for status in ('pending', 'cancelled', 'delivered'):
            with self.subTest(status=status):
                notify_status_change_async(_order(status=status, order_code=f'ST-{status}'))
        mock_thread_cls.assert_not_called()

    @patch('orders.notifications.threading.Thread')
    def test_telefono_invalido_no_manda_whatsapp_pero_si_correo(self, mock_thread_cls):
        from orders.notifications import notify_status_change_async
        notify_status_change_async(_order(customer_phone='12345'))
        phone, email = mock_thread_cls.call_args[1]['args'][:2]
        self.assertEqual(phone, '')
        self.assertEqual(email, 'ana@example.com')

    @patch('orders.notifications.threading.Thread', side_effect=RuntimeError("can't start new thread"))
    def test_no_propaga_si_el_thread_no_arranca(self, mock_thread_cls):
        from orders.notifications import notify_status_change_async
        notify_status_change_async(_order())  # no debe lanzar


class SendStatusNoticeTests(TestCase):
    ARGS = ('5512345678', 'ana@example.com', 'Tu pedido #ST-1 — Confirmado', 'hola Ana', 'ST-1')

    @override_settings(**CORREO_OK)
    @patch('orders.notifications.urllib.request.urlopen')
    def test_manda_whatsapp_con_token_y_correo(self, mock_urlopen):
        from orders.notifications import _send_status_notice
        _send_status_notice(*self.ARGS)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, f'{settings.BOT_NOTIFY_URL}/notify')
        self.assertEqual(req.get_header('X-notify-token'), 'tok-123')
        self.assertEqual(
            json.loads(req.data.decode('utf-8')),
            {'message': 'hola Ana', 'target': 'customer', 'phone': '5512345678'},
        )

        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ['ana@example.com'])
        self.assertEqual(correo.subject, 'Tu pedido #ST-1 — Confirmado')
        self.assertEqual(correo.body, 'hola Ana')
        self.assertEqual(correo.from_email, 'ryal@gmail.com')

    @override_settings(**CORREO_OK)
    @patch('orders.notifications.urllib.request.urlopen')
    def test_sin_correo_del_cliente_solo_whatsapp(self, mock_urlopen):
        from orders.notifications import _send_status_notice
        _send_status_notice('5512345678', '', 'asunto', 'hola', 'ST-1')
        mock_urlopen.assert_called_once()
        self.assertEqual(mail.outbox, [])

    @override_settings(**{**CORREO_OK, 'EMAIL_HOST_USER': '', 'EMAIL_HOST_PASSWORD': ''})
    @patch('orders.notifications.urllib.request.urlopen')
    def test_sin_credenciales_smtp_no_sale_correo_y_whatsapp_si(self, mock_urlopen):
        from orders.notifications import _send_status_notice
        _send_status_notice(*self.ARGS)
        mock_urlopen.assert_called_once()
        self.assertEqual(mail.outbox, [])

    @override_settings(**{**CORREO_OK, 'NOTIFY_TOKEN': ''})
    @patch('orders.notifications.urllib.request.urlopen')
    def test_sin_token_no_hay_post_pero_si_correo(self, mock_urlopen):
        from orders.notifications import _send_status_notice
        _send_status_notice(*self.ARGS)
        mock_urlopen.assert_not_called()
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(**CORREO_OK)
    @patch('orders.notifications.urllib.request.urlopen', side_effect=OSError('conexión rechazada'))
    def test_bot_caido_el_correo_sale_igual(self, mock_urlopen):
        from orders.notifications import _send_status_notice
        _send_status_notice(*self.ARGS)  # no debe lanzar
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(**CORREO_OK)
    @patch('orders.notifications.send_mail', side_effect=OSError('smtp caído'))
    @patch('orders.notifications.urllib.request.urlopen')
    def test_smtp_caido_no_propaga(self, mock_urlopen, mock_send_mail):
        from orders.notifications import _send_status_notice
        _send_status_notice(*self.ARGS)  # no debe lanzar
        mock_urlopen.assert_called_once()
        mock_send_mail.assert_called_once()

    @override_settings(**CORREO_OK)
    @patch('orders.notifications.urllib.request.urlopen')
    def test_sin_telefono_no_hay_post(self, mock_urlopen):
        from orders.notifications import _send_status_notice
        _send_status_notice('', 'ana@example.com', 'asunto', 'hola', 'ST-1')
        mock_urlopen.assert_not_called()
        self.assertEqual(len(mail.outbox), 1)
