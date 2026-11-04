"""PDF de etiquetas 54 × 40 mm (una página por par) para la impresora de rollo."""
import io
import re

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from qrcode.image.svg import SvgPathImage
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from inventario.models import fmt_talla

# 40 mm de alto (antes 44): en el rollo continuo, cada mm de alto es papel por etiqueta.
ANCHO, ALTO = 54 * mm, 40 * mm
MARGEN = 3 * mm          # lateral
MARGEN_SUP = 2.5 * mm
QR_LADO = 24 * mm
FUENTE, NEGRITA = 'Helvetica', 'Helvetica-Bold'
VIAS = {'aereo': 'AÉREO', 'maritimo': 'MARÍTIMO', 'nacional': 'NACIONAL'}


def url_par(base_url, codigo):
    return f'{base_url.rstrip("/")}/panel/inventario/p/{codigo}/'


def _qr(texto):
    """Mismos parámetros para el QR impreso y el de la pantalla."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=1)
    qr.add_data(texto)
    qr.make(fit=True)
    return qr


def _imagen_qr(texto):
    return ImageReader(_qr(texto).make_image(fill_color='black', back_color='white').get_image())


def qr_svg(texto):
    """El QR como <svg> en línea (sin declaración XML), para mostrarlo en el panel."""
    svg = _qr(texto).make_image(image_factory=SvgPathImage).to_string(encoding='unicode')
    return re.sub(r'^<\?xml[^>]*\?>\s*', '', svg)


def _ajustar(texto, fuente, tam, ancho, minimo=5):
    """Reduce el tamaño hasta que el texto quepa en `ancho`; devuelve (texto, tam).

    Si ni al mínimo cabe, lo recorta con puntos suspensivos."""
    while tam > minimo and stringWidth(texto, fuente, tam) > ancho:
        tam -= 0.5
    if stringWidth(texto, fuente, tam) > ancho:
        while len(texto) > 1 and stringWidth(texto + '…', fuente, tam) > ancho:
            texto = texto[:-1]
        texto = texto.rstrip() + '…'
    return texto, tam


def _partir(texto, fuente, tam, ancho):
    """Una línea si cabe a tamaño completo; si no, el corte en 2 líneas (en un espacio o
    después de '/') que deja la línea más ancha lo más angosta posible."""
    if stringWidth(texto, fuente, tam) <= ancho:
        return [texto]
    cortes = [i + 1 for i, ch in enumerate(texto[:-1]) if ch in ' /']
    if not cortes:
        return [texto]
    mejor = min(cortes, key=lambda i: max(stringWidth(texto[:i].strip(), fuente, tam),
                                          stringWidth(texto[i:].strip(), fuente, tam)))
    return [texto[:mejor].strip(), texto[mejor:].strip()]


def _dos_lineas(c, texto, fuente, tam, x, y, ancho):
    """Dibuja `texto` en hasta 2 líneas, achicando solo la línea que no quepa.
    Devuelve la `y` de la línea siguiente."""
    for linea in _partir(texto, fuente, tam, ancho):
        t, tam_linea = _ajustar(linea, fuente, tam, ancho)
        c.setFont(fuente, tam_linea)
        c.drawString(x, y, t)
        y -= tam + 1
    return y


def equivalencias(talla):
    partes = []
    if talla.eu is not None:
        partes.append(f'EU {fmt_talla(talla.eu)}')
    if talla.us_hombre is not None:
        partes.append(f'US H {fmt_talla(talla.us_hombre)}')
    if talla.us_mujer is not None:
        partes.append(f'US M {fmt_talla(talla.us_mujer)}')
    return ' · '.join(partes)


# Tamaños (pt). Subidos tras la primera impresión de prueba en la PM290.
T_CODIGO, T_EQUIV = 9.5, 8.5
T_MODELO, T_VIA, T_COLOR, T_MX = 9, 7.5, 8.5, 30


def _dibujar(c, par, base_url):
    # Columna izquierda: QR, código debajo y, pegadas al código, las equivalencias.
    qr_x, qr_y = MARGEN, ALTO - MARGEN_SUP - QR_LADO
    c.drawImage(_imagen_qr(url_par(base_url, par.codigo)), qr_x, qr_y, QR_LADO, QR_LADO)
    y_codigo = qr_y - T_CODIGO - 1
    texto, tam = _ajustar(par.codigo, NEGRITA, T_CODIGO, QR_LADO + 2 * mm)
    c.setFont(NEGRITA, tam)
    c.drawCentredString(qr_x + QR_LADO / 2, y_codigo, texto)

    # Equivalencias a todo lo ancho, justo debajo del código; se omiten las que no existen.
    eq = equivalencias(par.talla)
    if eq:
        texto, tam = _ajustar(eq, FUENTE, T_EQUIV, ANCHO - 2 * MARGEN)
        c.setFont(FUENTE, tam)
        c.drawString(MARGEN, y_codigo - T_EQUIV - 3, texto)

    # Columna derecha: modelo, vía, color y talla MX grande.
    x = qr_x + QR_LADO + 2 * mm
    ancho = ANCHO - MARGEN - x
    y = ALTO - MARGEN_SUP - T_MODELO + 1
    y = _dos_lineas(c, par.tipo.modelo_base.nombre, NEGRITA, T_MODELO, x, y, ancho)
    c.setFont(FUENTE, T_VIA)
    c.drawString(x, y, VIAS.get(par.tipo.via, (par.tipo.via or '').upper()))
    y -= T_VIA + 2
    y = _dos_lineas(c, par.color.nombre.upper(), FUENTE, T_COLOR, x, y, ancho) + T_COLOR + 1
    texto, tam = _ajustar(fmt_talla(par.talla.mx_cm), NEGRITA, T_MX, ancho, minimo=14)
    # La talla MX debajo del color, sin bajar de la línea del código.
    y = max(y - tam * 0.85, y_codigo)
    c.setFont(NEGRITA, tam)
    c.drawString(x, y, texto)


def pdf_etiquetas(pares, *, base_url):
    """Un PDF con una etiqueta por par (tipo, color y talla ya cargados si son muchos)."""
    buf = io.BytesIO()
    # pageCompression=0: PDFs de pocas páginas, y así el contenido es verificable en tests.
    c = canvas.Canvas(buf, pagesize=(ANCHO, ALTO), pageCompression=0)
    c.setTitle('Etiquetas de pares')
    for par in pares:
        _dibujar(c, par, base_url)
        c.showPage()
    c.save()
    return buf.getvalue()
