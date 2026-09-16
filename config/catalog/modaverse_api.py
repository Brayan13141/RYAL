# -*- coding: utf-8 -*-
"""Cliente de la API pública de modaverse.vip.

Hoy la API no pide autenticación. Si algún día la pide, el punto de cambio es
este módulo y nada más: por eso el cliente HTTP vive separado del armador del
carrito. Ver la sección "Contingencia" del spec.
"""
import httpx

API_BASE = 'https://api.modaverse.vip/kkd_boot'
TIMEOUT  = 20.0

HEADERS = {
    'Content-Type': 'application/json',
    'Referer':      'https://www.modaverse.vip/',
    'Origin':       'https://www.modaverse.vip',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}

# Un 'success: false' con alguna de estas palabras no es "no existe el producto",
# es "la API dejó de ser pública".
_AUTH_HINTS = ('token', 'login', 'auth', 'unauthorized', 'forbidden', '登录', '未登录')


class ModaverseUnavailable(RuntimeError):
    """La API no respondió, o dejó de ser pública (auth, challenge, caída)."""


def new_client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)


def get_product(pid: str, client=None) -> dict | None:
    """Detalle del producto por productId.

    Devuelve el dict del producto, o None si la API dice que no existe.
    Lanza ModaverseUnavailable si la API no está accesible.
    """
    own = client is None
    c = client or new_client()
    try:
        resp = _post_with_retry(c, '/product/getProductById', {'productId': pid})
    finally:
        if own:
            c.close()

    try:
        data = resp.json()
    except ValueError:
        # HTML donde debería haber JSON: pantalla de login o challenge.
        raise ModaverseUnavailable(
            f'La API respondió algo que no es JSON (HTTP {resp.status_code}). '
            f'Es probable que ya no sea pública.'
        )

    if not data.get('success'):
        msg = str(data.get('message') or '')
        if any(h in msg.lower() for h in _AUTH_HINTS):
            raise ModaverseUnavailable(f'La API rechazó la consulta: {msg}')
        return None

    return data.get('data') or None


def _post_with_retry(client, path: str, payload: dict):
    url = API_BASE + path
    ultimo = None
    for intento in (1, 2):
        try:
            resp = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            ultimo = f'{type(exc).__name__}: {exc}'
            continue
        if resp.status_code in (401, 403):
            raise ModaverseUnavailable(
                f'La API devolvió HTTP {resp.status_code}: ya no es pública o exige token.'
            )
        if resp.status_code == 429:
            # Rate limit, no "no existe": si lo tratáramos como éxito con
            # success=false, un bloqueo temporal se confundiría con un
            # producto faltante y el carrito quedaría incompleto sin avisar.
            raise ModaverseUnavailable(
                'La API devolvió HTTP 429: está limitando la tasa de peticiones.'
            )
        if resp.status_code >= 500:
            ultimo = f'HTTP {resp.status_code}'
            continue
        return resp
    raise ModaverseUnavailable(f'La API no respondió tras 2 intentos ({ultimo}).')
