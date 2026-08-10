import json
import logging
import threading
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def _build_message(order):
    link = f'{settings.SITE_URL}/panel/pedidos/{order.pk}/'
    return (
        f'🛒 Nuevo pedido web #{order.order_code} de {order.customer_name} '
        f'— ${order.total:.0f} MXN. Ver: {link}'
    )


def _post_notify(message, order_code):
    """POST al servidor /notify del bot. Best-effort: atrapa todo y solo loguea."""
    body = json.dumps({'message': message, 'target': 'orders'}).encode('utf-8')
    req = urllib.request.Request(
        f'{settings.BOT_NOTIFY_URL}/notify',
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        logger.warning('Sin confirmación del bot para el pedido web #%s: %s', order_code, e)


def notify_new_order(order):
    """Arma el mensaje y lo manda. Síncrona y best-effort — nunca propaga."""
    try:
        message = _build_message(order)
    except Exception as e:
        logger.warning('No se pudo armar el aviso del pedido web #%s: %s', order.order_code, e)
        return
    _post_notify(message, order.order_code)


def notify_new_order_async(order):
    """Arma el mensaje en el thread del request (conexión de DB ya caliente) y
    manda solo el POST al thread aparte — sin ORM en el thread spawneado."""
    try:
        message = _build_message(order)
        order_code = order.order_code
    except Exception as e:
        logger.warning('No se pudo armar el aviso del pedido web #%s: %s', order.order_code, e)
        return
    threading.Thread(target=_post_notify, args=(message, order_code), daemon=True).start()
