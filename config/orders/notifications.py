import json
import logging
import threading
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def notify_new_order(order):
    """Avisa al bot de WhatsApp (Grupo Pedidos) que hay un pedido web nuevo.
    Síncrona y best-effort: atrapa cualquier error de red/HTTP y solo loguea.
    Usar notify_new_order_async desde request handlers — esta función bloquea."""
    link = f'{settings.SITE_URL}/panel/pedidos/{order.pk}/'
    message = (
        f'🛒 Nuevo pedido web #{order.order_code} de {order.customer_name} '
        f'— ${order.total:.0f} MXN. Ver: {link}'
    )
    body = json.dumps({'message': message, 'target': 'orders'}).encode('utf-8')
    req = urllib.request.Request(
        f'{settings.BOT_NOTIFY_URL}/notify',
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        urllib.request.urlopen(req, timeout=3)
    except Exception as e:
        logger.warning('No se pudo notificar pedido web #%s al bot: %s', order.order_code, e)


def notify_new_order_async(order):
    """Dispara notify_new_order en un thread aparte — nunca bloquea al caller."""
    threading.Thread(target=notify_new_order, args=(order,), daemon=True).start()
