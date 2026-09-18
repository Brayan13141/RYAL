import itertools
from datetime import timedelta

from django.contrib import admin
from django.test import TestCase
from django.utils import timezone

from orders.admin import OrderAdmin
from orders.models import InvalidTransition, Order

URL_17TRACK = 'https://t.17track.net/en#nums=JMX123456789'
_seq = itertools.count(1)


def _order(status='pending', **kw):
    return Order.objects.create(
        order_code=f'SM-{next(_seq)}', customer_name='Ana López',
        customer_phone='5512345678', status=status, **kw,
    )


class TransicionesValidasTests(TestCase):
    def test_cadena_completa_hasta_entregado(self):
        order = _order()
        order.transition_to('confirmed')
        order.transition_to('in_preparation')
        order.transition_to('shipped', tracking_url=URL_17TRACK)
        order.transition_to('delivered')
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertEqual(order.tracking_url, URL_17TRACK)

    def test_cada_paso_queda_guardado_en_la_bd(self):
        order = _order()
        order.transition_to('confirmed')
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'confirmed')

    def test_se_cancela_desde_cualquier_estado_previo_al_envio(self):
        for inicial in ('pending', 'confirmed', 'in_preparation'):
            with self.subTest(inicial=inicial):
                order = _order(status=inicial)
                order.transition_to('cancelled')
                self.assertEqual(Order.objects.get(pk=order.pk).status, 'cancelled')

    def test_enviado_guarda_la_url_de_17track(self):
        order = _order(status='in_preparation')
        order.transition_to('shipped', tracking_url=f'  {URL_17TRACK}  ')
        order.refresh_from_db()
        self.assertEqual(order.status, 'shipped')
        self.assertEqual(order.tracking_url, URL_17TRACK)


class TransicionesInvalidasTests(TestCase):
    def _assert_rechaza(self, inicial, nuevo, tracking_url=''):
        order = _order(status=inicial)
        with self.assertRaises(InvalidTransition):
            order.transition_to(nuevo, tracking_url=tracking_url)
        self.assertEqual(Order.objects.get(pk=order.pk).status, inicial)

    def test_no_se_saltan_pasos(self):
        casos = [
            ('pending', 'in_preparation'), ('pending', 'shipped'), ('pending', 'delivered'),
            ('confirmed', 'shipped'), ('confirmed', 'delivered'), ('in_preparation', 'delivered'),
        ]
        for inicial, nuevo in casos:
            with self.subTest(inicial=inicial, nuevo=nuevo):
                self._assert_rechaza(inicial, nuevo, tracking_url=URL_17TRACK)

    def test_no_se_regresa(self):
        casos = [
            ('confirmed', 'pending'), ('in_preparation', 'confirmed'),
            ('shipped', 'in_preparation'), ('delivered', 'shipped'),
        ]
        for inicial, nuevo in casos:
            with self.subTest(inicial=inicial, nuevo=nuevo):
                self._assert_rechaza(inicial, nuevo)

    def test_estados_finales_y_enviado_no_se_cancelan(self):
        for inicial, nuevo in [('delivered', 'cancelled'), ('cancelled', 'confirmed'), ('shipped', 'cancelled')]:
            with self.subTest(inicial=inicial, nuevo=nuevo):
                self._assert_rechaza(inicial, nuevo)

    def test_mismo_estado_se_rechaza(self):
        self._assert_rechaza('confirmed', 'confirmed')

    def test_estado_inexistente_se_rechaza(self):
        self._assert_rechaza('pending', 'volando')

    def test_mensaje_para_el_staff_nombra_los_dos_estados(self):
        order = _order()
        with self.assertRaisesMessage(InvalidTransition, 'No se puede pasar de "Pendiente" a "Enviado".'):
            order.transition_to('shipped', tracking_url=URL_17TRACK)

    def test_instancia_vieja_no_repite_la_transicion(self):
        # Doble clic: dos requests leen "Pendiente"; el segundo no debe volver a guardar.
        order = _order()
        vieja = Order.objects.get(pk=order.pk)
        order.transition_to('confirmed')
        with self.assertRaisesMessage(InvalidTransition, 'El pedido cambió de estado mientras tanto.'):
            vieja.transition_to('confirmed')
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'confirmed')

    def test_la_transicion_actualiza_updated_at(self):
        order = _order()
        antes = timezone.now() - timedelta(days=1)
        Order.objects.filter(pk=order.pk).update(updated_at=antes)
        order.transition_to('confirmed')
        self.assertGreater(Order.objects.get(pk=order.pk).updated_at, antes)

    def test_enviado_exige_url_http_o_https(self):
        for url in ('', '   ', 'javascript:alert(1)', 'ftp://x.com/guia', 'no es una url'):
            with self.subTest(url=url):
                order = _order(status='in_preparation')
                with self.assertRaises(InvalidTransition):
                    order.transition_to('shipped', tracking_url=url)
                order.refresh_from_db()
                self.assertEqual(order.status, 'in_preparation')
                self.assertEqual(order.tracking_url, '')


class SiguientesEstadosTests(TestCase):
    def test_pendiente_ofrece_confirmar_o_cancelar(self):
        self.assertEqual(
            _order().allowed_next_statuses(),
            [('confirmed', 'Confirmado'), ('cancelled', 'Cancelado')],
        )

    def test_entregado_es_final(self):
        order = _order(status='delivered')
        self.assertEqual(order.allowed_next_statuses(), [])
        self.assertTrue(order.is_final_status)
        self.assertFalse(_order(status='shipped').is_final_status)


class AdminNoBrincaLaMaquinaTests(TestCase):
    def test_status_y_tracking_url_son_de_solo_lectura(self):
        model_admin = OrderAdmin(Order, admin.site)
        self.assertNotIn('status', model_admin.list_editable)
        self.assertIn('status', model_admin.readonly_fields)
        self.assertIn('tracking_url', model_admin.readonly_fields)
