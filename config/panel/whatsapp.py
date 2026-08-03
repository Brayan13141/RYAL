import json

from django.conf import settings

WHATSAPP_INSTANCES = [
    {
        'key': 'persona1',
        'label': 'Persona 1 (reenvío proveedor)',
        'phone': '4439728793',
        'state_path': settings.BASE_DIR.parent / 'bot' / '.qr_state.json',
    },
    {
        'key': 'persona2',
        'label': 'Persona 2 (pedidos)',
        'phone': '4451129186',
        'state_path': settings.BASE_DIR.parent / 'bot-p2' / '.qr_state.json',
    },
    {
        'key': 'bot-4451076015',
        'label': 'Bot 4451076015 (solo privados)',
        'phone': '4451076015',
        'state_path': settings.BASE_DIR.parent / 'bot-4451076015' / '.qr_state.json',
    },
]


def get_instance(key):
    return next((i for i in WHATSAPP_INSTANCES if i['key'] == key), None)


def read_qr_state(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return {
            'status': data.get('status', 'no_data'),
            'qr': data.get('qr'),
            'updated_at': data.get('updated_at'),
        }
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError, TypeError):
        return {'status': 'no_data', 'qr': None, 'updated_at': None}
