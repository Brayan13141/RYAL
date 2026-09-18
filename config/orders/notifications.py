import json
import logging
import threading
import urllib.request

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from negocio.phone import normalize_telefono

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


# ——— Avisos al cliente por cambio de estado ———

NOTIFY_STATUSES = ('confirmed', 'in_preparation', 'shipped')

_STATUS_MESSAGES = {
    'confirmed':      '✅ Hola {nombre}, tu pedido #{code} fue confirmado. Síguelo aquí: {link}',
    'in_preparation': '📦 Hola {nombre}, tu pedido #{code} ya se está preparando. Síguelo aquí: {link}',
    'shipped':        '🚚 Hola {nombre}, tu pedido #{code} ya va en camino. Rastréalo aquí: {link}',
}


def build_status_notice(order):
    """(asunto, mensaje) del aviso al cliente para el estado actual del pedido."""
    if order.status == 'shipped':
        link = order.tracking_url
    else:
        link = settings.SITE_URL + reverse('orders:confirmation', args=[order.tracking_token])
    nombre = (order.customer_name.split() or ['cliente'])[0]
    message = _STATUS_MESSAGES[order.status].format(nombre=nombre, code=order.order_code, link=link)
    subject = f'Tu pedido #{order.order_code} — {order.get_status_display()}'
    return subject, message


def _post_notify_customer(phone, message, order_code):
    """WhatsApp al cliente vía /notify del bot. Best-effort: atrapa todo y solo loguea."""
    if not settings.NOTIFY_TOKEN:
        logger.warning('NOTIFY_TOKEN vacío: no se avisó por WhatsApp el pedido #%s', order_code)
        return
    try:
        body = json.dumps({'message': message, 'target': 'customer', 'phone': phone}).encode('utf-8')
        req = urllib.request.Request(
            f'{settings.BOT_NOTIFY_URL}/notify',
            data=body,
            headers={'Content-Type': 'application/json', 'X-Notify-Token': settings.NOTIFY_TOKEN},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        logger.warning('El bot no confirmó el aviso de estado del pedido #%s: %s', order_code, e)


def _send_status_email(email, subject, message, order_code):
    """Correo al cliente. Se apaga solo si no hay correo o no hay credenciales SMTP."""
    if not email:
        return
    if not (settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD):
        logger.info('Correo sin configurar: no se mandó el aviso de estado del pedido #%s', order_code)
        return
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    except Exception as e:
        logger.warning('No salió el correo de estado del pedido #%s: %s', order_code, e)


def _send_status_notice(phone, email, subject, message, order_code):
    """Corre en el thread aparte. Los canales van cada uno por su lado: si uno falla, el otro sale."""
    if phone:
        _post_notify_customer(phone, message, order_code)
    _send_status_email(email, subject, message, order_code)


def notify_status_change_async(order):
    """Avisa al cliente el estado nuevo. Nunca propaga: el cambio de estado ya está guardado.

    Arma todo en el thread del request (sin ORM en el thread spawneado) y manda
    los dos canales en un thread daemon.
    """
    try:
        if order.status not in NOTIFY_STATUSES:
            return
        subject, message = build_status_notice(order)
        phone = normalize_telefono(order.customer_phone)
        if len(phone) != 10:
            logger.warning('Teléfono inválido en el pedido #%s: no se avisa por WhatsApp', order.order_code)
            phone = ''
        email = (order.customer_email or '').strip()
        threading.Thread(
            target=_send_status_notice,
            args=(phone, email, subject, message, order.order_code),
            daemon=True,
        ).start()
    except Exception as e:
        logger.warning('No se pudo lanzar el aviso de estado del pedido #%s: %s',
                       getattr(order, 'order_code', '?'), e)
