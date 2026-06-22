from allauth.account.adapter import DefaultAccountAdapter
from django.core.exceptions import PermissionDenied


class AccountAdapter(DefaultAccountAdapter):
    def get_client_ip(self, request):
        """
        IP real del cliente. Usa X-Real-IP (seteado por Nginx desde $remote_addr,
        no spoofeable) en primer lugar. Compatible con Unix socket (REMOTE_ADDR vacío).
        """
        real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
        if real_ip:
            return real_ip
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            # Último elemento = el que Nginx agregó; el primero puede ser falsificado
            return xff.split(",")[-1].strip()
        ip = request.META.get("REMOTE_ADDR", "")
        if not ip:
            raise PermissionDenied("Unable to determine client IP address")
        return ip
