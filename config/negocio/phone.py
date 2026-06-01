def normalize_telefono(value):
    """Reduce un teléfono a sus últimos 10 dígitos.

    WhatsApp entrega el JID como '521'/'52' + 10 dígitos; en el panel se
    capturan formatos variados (espacios, +52, paréntesis). Normalizar a los
    últimos 10 dígitos hace que el lookup del bot coincida con lo guardado.
    """
    return ''.join(c for c in str(value) if c.isdigit())[-10:]
