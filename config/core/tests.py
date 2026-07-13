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
