class ContentSecurityPolicyMiddleware:
    """
    Adds Content-Security-Policy header to every response.

    NOTE: script-src includes 'unsafe-inline' because templates use inline <script> blocks.
    To fully harden XSS protection, migrate inline scripts to external files or use nonces.
    CDL inventory: Bootstrap/Icons/SortableJS/Chart.js all served from cdn.jsdelivr.net;
    fonts from fonts.googleapis.com (CSS) and fonts.gstatic.com (font files).
    """

    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net fonts.googleapis.com; "
        "font-src 'self' data: fonts.gstatic.com cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
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
