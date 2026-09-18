from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from orders.models import Order

URL_17TRACK = 'https://t.17track.net/en#nums=JMX123456789'
AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


class OrderStatusUpdateTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='staff_estado', password='pass', is_staff=True)
        self.client.login(username='staff_estado', password='pass')
        self.order = Order.objects.create(
            order_code='EST-1', customer_name='Ana López', customer_phone='5512345678',
            customer_email='ana@example.com',
        )
        self.order.items.create(
            product=None, quantity=1, price_snapshot=Decimal('450'), sku_snapshot='X', name_snapshot='X',
        )
        self.url = reverse('panel:order_status_update', args=[self.order.pk])

    def _set_status(self, status):
        Order.objects.filter(pk=self.order.pk).update(status=status)

    @patch('orders.notifications.threading.Thread')
    def test_transicion_valida_guarda_y_avisa_una_vez(self, mock_thread_cls):
        resp = self.client.post(self.url, {'status': 'confirmed'}, **AJAX)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'ok': True, 'status': 'confirmed', 'label': 'Confirmado'})
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, 'confirmed')
        mock_thread_cls.assert_called_once()
        message = mock_thread_cls.call_args[1]['args'][3]
        self.assertIn('fue confirmado', message)
        self.assertIn('#EST-1', message)

    @patch('orders.notifications.threading.Thread')
    def test_transicion_invalida_400_sin_cambio_y_sin_aviso(self, mock_thread_cls):
        resp = self.client.post(self.url, {'status': 'shipped', 'tracking_url': URL_17TRACK}, **AJAX)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])
        self.assertIn('No se puede pasar de "Pendiente" a "Enviado"', resp.json()['error'])
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, 'pending')
        mock_thread_cls.assert_not_called()

    @patch('orders.notifications.threading.Thread')
    def test_enviado_sin_url_se_rechaza_y_con_url_avisa_con_ella(self, mock_thread_cls):
        self._set_status('in_preparation')
        resp = self.client.post(self.url, {'status': 'shipped'}, **AJAX)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, 'in_preparation')

        resp = self.client.post(self.url, {'status': 'shipped', 'tracking_url': URL_17TRACK}, **AJAX)
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(pk=self.order.pk)
        self.assertEqual((order.status, order.tracking_url), ('shipped', URL_17TRACK))
        self.assertIn(URL_17TRACK, mock_thread_cls.call_args[1]['args'][3])

    @patch('orders.notifications.threading.Thread')
    def test_cancelado_y_entregado_no_avisan(self, mock_thread_cls):
        resp = self.client.post(self.url, {'status': 'cancelled'}, **AJAX)
        self.assertEqual(resp.status_code, 200)
        self._set_status('shipped')
        resp = self.client.post(self.url, {'status': 'delivered'}, **AJAX)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, 'delivered')
        mock_thread_cls.assert_not_called()

    @patch('orders.notifications.threading.Thread')
    def test_no_ajax_invalido_redirige_con_mensaje(self, mock_thread_cls):
        resp = self.client.post(self.url, {'status': 'delivered'})
        self.assertRedirects(resp, reverse('panel:order_detail', args=[self.order.pk]), fetch_redirect_response=False)
        mensajes = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertEqual(mensajes, ['No se puede pasar de "Pendiente" a "Entregado".'])
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, 'pending')

    @patch('orders.notifications.threading.Thread')
    def test_no_staff_no_cambia_nada(self, mock_thread_cls):
        self.client.logout()
        resp = self.client.post(self.url, {'status': 'confirmed'}, **AJAX)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, 'pending')
        mock_thread_cls.assert_not_called()

    def test_detalle_ofrece_solo_los_estados_siguientes(self):
        resp = self.client.get(reverse('panel:order_detail', args=[self.order.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<option value="confirmed">Confirmado</option>', html=True)
        self.assertContains(resp, '<option value="cancelled">Cancelado</option>', html=True)
        self.assertNotContains(resp, 'value="shipped"')
        self.assertNotContains(resp, 'value="delivered"')

    def test_detalle_de_pedido_final_no_tiene_selector(self):
        self._set_status('delivered')
        resp = self.client.get(reverse('panel:order_detail', args=[self.order.pk]))
        self.assertContains(resp, 'Estado final — no admite cambios.')
        self.assertNotContains(resp, 'id="statusSelect"')

    def test_detalle_muestra_la_url_de_rastreo(self):
        Order.objects.filter(pk=self.order.pk).update(status='shipped', tracking_url=URL_17TRACK)
        resp = self.client.get(reverse('panel:order_detail', args=[self.order.pk]))
        self.assertContains(resp, f'href="{URL_17TRACK}"')


class ConfirmationTrackingLinkTests(TestCase):
    def test_pagina_publica_muestra_rastrear_envio_solo_si_hay_url(self):
        order = Order.objects.create(
            order_code='EST-PUB', customer_name='Ana', customer_phone='5512345678',
        )
        url = reverse('orders:confirmation', args=[order.tracking_token])
        self.assertNotContains(self.client.get(url), 'Rastrear envío')

        Order.objects.filter(pk=order.pk).update(status='shipped', tracking_url=URL_17TRACK)
        resp = self.client.get(url)
        self.assertContains(resp, 'Rastrear envío')
        self.assertContains(resp, f'href="{URL_17TRACK}"')
