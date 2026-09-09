"""Clave de rate limit común a todo el proyecto.

Nginx habla con Gunicorn por un socket Unix, así que `REMOTE_ADDR` llega vacío y
la clave `'ip'` de django-ratelimit lanza `ImproperlyConfigured` — la vista
entera devuelve 500 antes de ejecutarse. Hay que leer la IP real de las
cabeceras que pone el proxy.

De `X-Forwarded-For` se toma el **último** valor, no el primero: el cliente
puede enviar la cabecera ya poblada, y solo lo que el proxy añade al final es
de fiar.
"""


def client_ip(group, request):
    """IP real del cliente. django-ratelimit 4.x llama con (group, request)."""
    real_ip = request.META.get('HTTP_X_REAL_IP', '').strip()
    if real_ip:
        return real_ip
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', '') or '127.0.0.1'
