from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.http import HttpResponse


_BYPASS_PREFIXES = ('/admin/', '/accounts/login/')


class MaintenanceModeMiddleware:
    """
    Intercepts all requests when the maintenance flag file exists and returns
    a 503 response.  Staff users and a small set of management paths are
    always allowed through so the site can be managed while offline.

    Toggle (no gunicorn restart required):
        activate:   touch /root/app/maintenance.flag
        deactivate: rm    /root/app/maintenance.flag

    The flag path is controlled by settings.MAINTENANCE_FLAG_PATH
    (default: project root / 'maintenance.flag').
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_active() and not self._bypass(request):
            html = render_to_string('503_maintenance.html')
            response = HttpResponse(html, status=503, content_type='text/html; charset=utf-8')
            response['Retry-After'] = '3600'
            return response
        return self.get_response(request)

    # ------------------------------------------------------------------

    @staticmethod
    def _flag_path():
        configured = getattr(settings, 'MAINTENANCE_FLAG_PATH', None)
        if configured:
            return Path(configured)
        # Default: one level above manage.py (project root on the server)
        return Path(settings.BASE_DIR).parent / 'maintenance.flag'

    @classmethod
    def _is_active(cls):
        return cls._flag_path().exists()

    @staticmethod
    def _bypass(request):
        # AuthenticationMiddleware runs after us, so request.user may not exist yet
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_staff', False):
            return True
        return any(request.path.startswith(prefix) for prefix in _BYPASS_PREFIXES)


class ContentSecurityPolicyMiddleware:
    """
    Adds Content-Security-Policy header to every response.

    NOTE: script-src includes 'unsafe-inline' because templates use inline <script> blocks.
    To fully harden XSS protection, migrate inline scripts to external files or use nonces.
    CDN inventory: Bootstrap/Icons/SortableJS/Chart.js all served from cdn.jsdelivr.net;
    fonts from fonts.googleapis.com (CSS) and fonts.gstatic.com (font files);
    Meta Pixel loads fbevents.js from connect.facebook.net and beacons/noscript
    fallback to www.facebook.com.
    """

    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net connect.facebook.net; "
        "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net fonts.googleapis.com; "
        "font-src 'self' data: fonts.gstatic.com cdn.jsdelivr.net; "
        "img-src 'self' data: blob: www.facebook.com; "
        "connect-src 'self' cdn.jsdelivr.net www.facebook.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none';"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('Content-Security-Policy', self._CSP)
        return response
