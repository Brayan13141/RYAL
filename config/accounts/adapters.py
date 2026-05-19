from allauth.account.adapter import DefaultAccountAdapter
from django.core.exceptions import PermissionDenied


class AccountAdapter(DefaultAccountAdapter):
    def get_client_ip(self, request):
        """
        Reads the real client IP from X-Forwarded-For when running behind
        a reverse proxy (Nginx). Falls back to REMOTE_ADDR for direct connections.
        """
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        ip = request.META.get("REMOTE_ADDR")
        if not ip:
            raise PermissionDenied("Unable to determine client IP address")
        return ip
