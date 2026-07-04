from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase


class ResumenGlobalViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_rg', password='pass', is_staff=True
        )
        self.client.login(username='staff_rg', password='pass')

    def test_accesible_staff(self):
        res = self.client.get('/panel/resumen-global/')
        self.assertEqual(res.status_code, 200)

    def test_redirige_anonimo(self):
        self.client.logout()
        res = self.client.get('/panel/resumen-global/')
        self.assertEqual(res.status_code, 302)

    def test_periodo_default_es_mes_actual(self):
        hoy = date.today()
        res = self.client.get('/panel/resumen-global/')
        self.assertEqual(res.context['mes'], f"{hoy.year}-{hoy.month:02d}")

    def test_periodo_todo(self):
        res = self.client.get('/panel/resumen-global/?mes=todo')
        self.assertEqual(res.context['periodo_label'], 'Todo el tiempo')

    def test_periodo_mes_especifico(self):
        res = self.client.get('/panel/resumen-global/?mes=2026-06')
        self.assertContains(res, 'Jun 2026')
