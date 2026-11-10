import tempfile
import os
from pathlib import Path

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse as _HttpResponse

from core.middleware import ContentSecurityPolicyMiddleware, MaintenanceModeMiddleware

User = get_user_model()

_DUMMY_RESPONSE = object()


def _get_response(_request):
    return _DUMMY_RESPONSE


class MaintenanceModeMiddlewareTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        # Archivo flag temporal — NO existe aún
        self.flag_file = Path(tempfile.mktemp(suffix='.flag'))
        self.settings_override = override_settings(
            MAINTENANCE_FLAG_PATH=self.flag_file
        )
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        if self.flag_file.exists():
            self.flag_file.unlink()

    # ------------------------------------------------------------------
    # Sin flag activo — todo pasa normalmente
    # ------------------------------------------------------------------

    def test_no_flag_passes_through(self):
        request = self.factory.get('/')
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertIs(response, _DUMMY_RESPONSE)

    # ------------------------------------------------------------------
    # Flag activo — visitantes reciben 503
    # ------------------------------------------------------------------

    def test_flag_returns_503_for_anonymous(self):
        self.flag_file.touch()
        request = self.factory.get('/')
        request.user = User()  # usuario anónimo (is_staff=False)
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertEqual(response.status_code, 503)

    def test_flag_returns_503_for_regular_user(self):
        self.flag_file.touch()
        user = User(is_staff=False)
        request = self.factory.get('/catalogo/')
        request.user = user
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertEqual(response.status_code, 503)

    # ------------------------------------------------------------------
    # Bypass — staff y rutas privilegiadas siempre pasan
    # ------------------------------------------------------------------

    def test_staff_bypasses_maintenance(self):
        self.flag_file.touch()
        user = User(is_staff=True)
        request = self.factory.get('/catalogo/')
        request.user = user
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertIs(response, _DUMMY_RESPONSE)

    def test_admin_url_bypasses_maintenance(self):
        self.flag_file.touch()
        request = self.factory.get('/admin/')
        request.user = User()
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertIs(response, _DUMMY_RESPONSE)

    def test_login_url_bypasses_maintenance(self):
        self.flag_file.touch()
        request = self.factory.get('/accounts/login/')
        request.user = User()
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertIs(response, _DUMMY_RESPONSE)

    # ------------------------------------------------------------------
    # Contenido de la respuesta 503
    # ------------------------------------------------------------------

    def test_503_response_contains_maintenance_text(self):
        self.flag_file.touch()
        request = self.factory.get('/')
        request.user = User()
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertIn(b'mantenimiento', response.content.lower())

    def test_503_sets_retry_after_header(self):
        self.flag_file.touch()
        request = self.factory.get('/')
        request.user = User()
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertIn('Retry-After', response)

    def test_flag_returns_503_when_user_not_set(self):
        # Simulates request arriving before AuthenticationMiddleware sets request.user
        self.flag_file.touch()
        request = self.factory.get('/')
        # request.user deliberately NOT set — should not raise AttributeError
        mw = MaintenanceModeMiddleware(_get_response)
        response = mw(request)
        self.assertEqual(response.status_code, 503)


class ContentSecurityPolicyMiddlewareTests(TestCase):
    """El Meta Pixel necesita cargar fbevents.js y mandar su beacon de tracking;
    si el CSP no lista esos dominios, el navegador los bloquea en silencio y
    herramientas como Meta Pixel Helper nunca lo detectan."""

    def setUp(self):
        self.factory = RequestFactory()

    def _csp(self):
        request = self.factory.get('/')
        mw = ContentSecurityPolicyMiddleware(lambda r: _HttpResponse('ok'))
        response = mw(request)
        return response['Content-Security-Policy']

    def test_script_src_allows_facebook_pixel_script(self):
        csp = self._csp()
        script_src = [d for d in csp.split(';') if d.strip().startswith('script-src')][0]
        self.assertIn('connect.facebook.net', script_src)

    def test_connect_src_allows_facebook_pixel_beacon(self):
        csp = self._csp()
        connect_src = [d for d in csp.split(';') if d.strip().startswith('connect-src')][0]
        self.assertIn('www.facebook.com', connect_src)

    def test_img_src_allows_facebook_pixel_noscript_fallback(self):
        csp = self._csp()
        img_src = [d for d in csp.split(';') if d.strip().startswith('img-src')][0]
        self.assertIn('www.facebook.com', img_src)


class PermissionsPolicyHeaderTests(TestCase):
    """Sin `Permissions-Policy` el navegador deja que cualquier iframe o script
    embebido pida cámara, micrófono o geolocalización en nombre del sitio."""

    def test_la_respuesta_trae_permissions_policy(self):
        resp = self.client.get('/')
        self.assertIn('Permissions-Policy', resp.headers)

    def test_desactiva_camara_microfono_y_geolocalizacion(self):
        resp = self.client.get('/')
        policy = resp.headers.get('Permissions-Policy', '')
        for feature in ('camera', 'microphone', 'geolocation'):
            self.assertIn(f'{feature}=()', policy)

    def test_escaner_permite_la_camara_del_propio_sitio(self):
        """Con `camera=()` el navegador niega getUserMedia sin preguntar: el escáner
        de inventario lee QR con la cámara y necesita `camera=(self)`."""
        for ruta in ('/panel/inventario/escanear/',):
            policy = self.client.get(ruta).headers.get('Permissions-Policy', '')
            self.assertIn('camera=(self)', policy, ruta)
            self.assertNotIn('camera=()', policy, ruta)
            self.assertIn('microphone=()', policy, ruta)

    def test_el_resto_del_panel_sigue_sin_camara(self):
        for ruta in ('/panel/', '/panel/inventario/escanear/recibir/', '/panel/inventario/lotes/',
                     '/panel/negocio/pos/'):
            policy = self.client.get(ruta).headers.get('Permissions-Policy', '')
            self.assertIn('camera=()', policy, ruta)


@override_settings(REQUIRE_STAFF_MFA=True)
class StaffDebeTener2FATests(TestCase):
    """El panel y /admin/ mueven dinero y datos de clientes. Una sola contraseña
    filtrada bastaba para entrar; el segundo factor es lo que lo impide.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        self.staff = User.objects.create_user(
            'staff_2fa', password='Prueba1234!', is_staff=True, is_superuser=True
        )

    def test_staff_sin_segundo_factor_no_entra_al_panel(self):
        self.client.force_login(self.staff)
        resp = self.client.get('/panel/')
        self.assertEqual(
            resp.status_code, 302,
            'Un staff sin 2FA entró al panel directo: no hay segundo factor exigido.',
        )
        self.assertIn('2fa', resp['Location'])

    def test_admin_login_no_se_salta_el_segundo_factor(self):
        """/admin/login/ es el login de Django: pide contraseña y nada más.
        Con el TOTP ya enrolado el middleware lo dejaba pasar, así que la
        contraseña sola abría el admin."""
        from allauth.mfa.models import Authenticator
        Authenticator.objects.create(
            user=self.staff, type=Authenticator.Type.TOTP, data={'secret': 'X'})
        resp = self.client.post(
            '/admin/login/', {'username': 'staff_2fa', 'password': 'Prueba1234!'})
        self.assertNotIn(
            '_auth_user_id', self.client.session,
            'La contraseña sola abrió sesión de staff por /admin/login/.',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def _login_con_codigo(self, secret):
        from allauth.mfa.totp.internal.auth import format_hotp_value, hotp_value,             yield_hotp_counters_from_time
        from django.core.cache import cache
        cache.clear()  # el código ya usado no se acepta dos veces
        r = self.client.post('/accounts/login/',
                             {'login': 'staff_2fa', 'password': 'Prueba1234!'})
        self.assertIn('/2fa/authenticate/', r['Location'])
        codigo = format_hotp_value(hotp_value(secret, next(yield_hotp_counters_from_time())))
        return self.client.post('/2fa/authenticate/', {'code': codigo})

    def _activar_totp(self):
        from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
        secret = generate_totp_secret()
        TOTP.activate(self.staff, secret)
        return secret

    def test_confiar_en_el_navegador_no_vuelve_a_pedir_el_codigo(self):
        secret = self._activar_totp()
        r = self._login_con_codigo(secret)
        self.assertIn('/2fa/trust/', r['Location'])
        self.assertContains(self.client.get('/2fa/trust/'), 'Confiar en este navegador')
        self.client.post('/2fa/trust/', {'action': 'trust'})
        self.assertIn('_auth_user_id', self.client.session)
        self.client.post('/logout/')
        self.assertNotIn('_auth_user_id', self.client.session)
        r = self.client.post('/accounts/login/',
                             {'login': 'staff_2fa', 'password': 'Prueba1234!'})
        self.assertNotIn('/2fa/authenticate/', r['Location'])
        self.assertIn('_auth_user_id', self.client.session)

    def test_sin_confiar_vuelve_a_pedir_el_codigo(self):
        secret = self._activar_totp()
        self._login_con_codigo(secret)
        self.client.post('/2fa/trust/', {'action': 'skip'})
        self.assertIn('_auth_user_id', self.client.session)
        self.client.post('/logout/')
        self.assertNotIn('_auth_user_id', self.client.session)
        r = self.client.post('/accounts/login/',
                             {'login': 'staff_2fa', 'password': 'Prueba1234!'})
        self.assertIn('/2fa/authenticate/', r['Location'])

    def test_la_sesion_dura_30_dias_y_se_renueva(self):
        from django.conf import settings
        self.assertEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 24 * 30)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.client.post('/accounts/login/', {'login': 'staff_2fa', 'password': 'Prueba1234!'})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_un_usuario_normal_no_queda_atrapado_en_el_flujo_de_2fa(self):
        from django.contrib.auth.models import User
        cliente = User.objects.create_user('cliente_normal', password='Prueba1234!')
        self.client.force_login(cliente)
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
