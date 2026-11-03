"""Ayuda contextual del panel: registry, context processor, interruptor y cobertura.

Portado de Space Cheer (core/help_registry.py). Diferencia de fondo: aquí no hay
roles, la ayuda es solo para staff y la llave es el nombre de la vista.
"""
import inspect

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import get_resolver, reverse

from core.help_registry import EXCLUDED_VIEWS, HELP_REGISTRY, get_help_cards

User = get_user_model()


def _pantallas(namespace):
    """Vistas del namespace cuyo código llama a render(): las que pintan una
    pantalla. Los endpoints POST/JSON quedan fuera solos."""
    resolver = get_resolver().namespace_dict[namespace][1]
    vistas = set()
    for pattern in resolver.url_patterns:
        if not getattr(pattern, 'name', None):
            continue
        func = inspect.unwrap(pattern.callback)
        try:
            src = inspect.getsource(func)
        except (OSError, TypeError):
            continue
        if 'render(' in src:
            vistas.add(f'{namespace}:{pattern.name}')
    return vistas


class RegistryCoverageTests(TestCase):
    """Falla si se agrega una pantalla del panel o de negocio sin ayuda."""

    def test_toda_pantalla_del_panel_tiene_ayuda(self):
        for ns in ('panel', 'negocio'):
            pantallas = _pantallas(ns)
            self.assertTrue(pantallas, f'no se detectaron pantallas en {ns}')
            for view in sorted(pantallas):
                with self.subTest(view=view):
                    self.assertTrue(
                        view in HELP_REGISTRY or view in EXCLUDED_VIEWS,
                        f'{view} no tiene ayuda: agrégala a HELP_REGISTRY '
                        f'o, si es imprimible, a EXCLUDED_VIEWS')

    def test_el_registry_no_tiene_vistas_inexistentes(self):
        existentes = _pantallas('panel') | _pantallas('negocio')
        for view in HELP_REGISTRY:
            with self.subTest(view=view):
                self.assertIn(view, existentes)

    def test_cada_pantalla_trae_al_menos_dos_tarjetas_no_vacias(self):
        for view, cards in HELP_REGISTRY.items():
            with self.subTest(view=view):
                self.assertGreaterEqual(len(cards), 2)
                self.assertTrue(all(c.strip() for c in cards))


class GetHelpCardsTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('helpstaff', password='x', is_staff=True)
        self.cliente = User.objects.create_user('helpcliente', password='x')

    def test_staff_recibe_las_tarjetas_de_la_vista(self):
        cards = get_help_cards('panel:dashboard', self.staff)
        self.assertEqual(cards, list(HELP_REGISTRY['panel:dashboard']))

    def test_anonimo_no_recibe_nada(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(get_help_cards('panel:dashboard', AnonymousUser()), [])

    def test_usuario_no_staff_no_recibe_nada(self):
        self.assertEqual(get_help_cards('panel:dashboard', self.cliente), [])

    def test_staff_que_apago_la_ayuda_no_recibe_nada(self):
        self.staff.profile.help_dismissed = True
        self.staff.profile.save()
        self.assertEqual(get_help_cards('panel:dashboard', self.staff), [])

    def test_vista_sin_registro_devuelve_vacio(self):
        self.assertEqual(get_help_cards('catalog:home', self.staff), [])


class PanelHelpRenderTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('helprender', password='x', is_staff=True)
        self.client.force_login(self.staff)

    def test_el_panel_pinta_el_modal_con_las_tarjetas(self):
        res = self.client.get(reverse('panel:dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="ryHelpModal"')
        self.assertContains(res, 'data-help-view="panel:dashboard"')
        self.assertEqual(res.context['page_help_cards'],
                         list(HELP_REGISTRY['panel:dashboard']))
        self.assertContains(res, HELP_REGISTRY['panel:dashboard'][0])

    def test_con_la_ayuda_apagada_no_hay_modal_y_si_boton_para_activarla(self):
        self.staff.profile.help_dismissed = True
        self.staff.profile.save()
        res = self.client.get(reverse('panel:dashboard'))
        self.assertNotContains(res, 'id="ryHelpModal"')
        self.assertContains(res, 'name="action" value="enable"')

    def test_la_tienda_publica_no_pinta_la_ayuda(self):
        res = self.client.get(reverse('catalog:home'))
        self.assertNotContains(res, 'id="ryHelpModal"')


class ToggleHelpTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('helptoggle', password='x', is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse('accounts:toggle_help')

    def _flag(self):
        self.staff.profile.refresh_from_db()
        return self.staff.profile.help_dismissed

    def test_dismiss_apaga_y_vuelve_a_la_pantalla(self):
        res = self.client.post(self.url, {'action': 'dismiss', 'next': '/panel/productos/'})
        self.assertRedirects(res, '/panel/productos/', fetch_redirect_response=False)
        self.assertTrue(self._flag())

    def test_enable_la_vuelve_a_encender(self):
        self.staff.profile.help_dismissed = True
        self.staff.profile.save()
        self.client.post(self.url, {'action': 'enable', 'next': '/panel/'})
        self.assertFalse(self._flag())

    def test_next_externo_no_se_sigue(self):
        res = self.client.post(self.url, {'action': 'dismiss', 'next': 'https://evil.example/'})
        self.assertRedirects(res, reverse('panel:dashboard'), fetch_redirect_response=False)

    def test_get_no_permitido(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_no_staff_no_puede_usarlo(self):
        cliente = User.objects.create_user('helpnostaff', password='x')
        self.client.force_login(cliente)
        self.client.post(self.url, {'action': 'dismiss', 'next': '/'})
        cliente.profile.refresh_from_db()
        self.assertFalse(cliente.profile.help_dismissed)
