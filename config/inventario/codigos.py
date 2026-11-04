import secrets

from .models import Par

# Sin 0/O/1/I/L para que la etiqueta impresa no se confunda al teclearla.
ALFABETO = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
LARGO_SUFIJO = 4


def generar_codigo(prefijo: str, *, intentos: int = 10) -> str:
    """Código `PREFIJO-XXXX` que no exista todavía en la tabla de pares."""
    for _ in range(intentos):
        sufijo = ''.join(secrets.choice(ALFABETO) for _ in range(LARGO_SUFIJO))
        codigo = f'{prefijo}-{sufijo}'
        if not Par.objects.filter(codigo=codigo).exists():
            return codigo
    raise RuntimeError('No se pudo generar un código único')
