"""PDF de etiquetas 54 × 44 mm (una página por par) para la impresora de rollo."""
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from inventario.models import fmt_talla

ANCHO, ALTO = 54 * mm, 44 * mm
MARGEN = 3 * mm
QR_LADO = 24 * mm
FUENTE, NEGRITA = 'Helvetica', 'Helvetica-Bold'
VIAS = {'aereo': 'AÉREO', 'maritimo': 'MARÍTIMO'}


def url_par(base_url, codigo):
    return f'{base_url.rstrip("/")}/panel/inventario/p/{codigo}/'


def _imagen_qr(texto):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=1)
    qr.add_data(texto)
    qr.make(fit=True)
    return ImageReader(qr.make_image(fill_color='black', back_color='white').get_image())


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


def _equivalencias(talla):
    partes = []
    if talla.eu is not None:
        partes.append(f'EU {fmt_talla(talla.eu)}')
    if talla.us_hombre is not None:
        partes.append(f'US H {fmt_talla(talla.us_hombre)}')
    if talla.us_mujer is not None:
        partes.append(f'US M {fmt_talla(talla.us_mujer)}')
    return ' · '.join(partes)


def _dibujar(c, par, base_url):
    # Columna izquierda: QR y código debajo.
    qr_x, qr_y = MARGEN, ALTO - MARGEN - QR_LADO
    c.drawImage(_imagen_qr(url_par(base_url, par.codigo)), qr_x, qr_y, QR_LADO, QR_LADO)
    c.setFont(NEGRITA, 7)
    c.drawCentredString(qr_x + QR_LADO / 2, qr_y - 8, par.codigo)

    # Columna derecha: modelo, vía, color y talla MX grande.
    x = qr_x + QR_LADO + 2 * mm
    ancho = ANCHO - MARGEN - x
    y = ALTO - MARGEN - 6
    lineas = simpleSplit(par.tipo.modelo_base.nombre, NEGRITA, 7.5, ancho)
    if len(lineas) > 2:
        lineas = [lineas[0], lineas[1] + ' ' + ' '.join(lineas[2:])]
    for linea in lineas:
        texto, tam = _ajustar(linea, NEGRITA, 7.5, ancho)
        c.setFont(NEGRITA, tam)
        c.drawString(x, y, texto)
        y -= 8
    c.setFont(FUENTE, 6.5)
    c.drawString(x, y, VIAS.get(par.tipo.via, (par.tipo.via or '').upper()))
    y -= 8
    texto, tam = _ajustar(par.color.nombre.upper(), FUENTE, 7, ancho)
    c.setFont(FUENTE, tam)
    c.drawString(x, y, texto)
    y -= 22
    texto, tam = _ajustar(fmt_talla(par.talla.mx_cm), NEGRITA, 24, ancho, minimo=12)
    c.setFont(NEGRITA, tam)
    c.drawString(x, y, texto)

    # Equivalencias a todo lo ancho, al pie; se omiten las que no existen.
    eq = _equivalencias(par.talla)
    if eq:
        texto, tam = _ajustar(eq, FUENTE, 6.5, ANCHO - 2 * MARGEN)
        c.setFont(FUENTE, tam)
        c.drawCentredString(ANCHO / 2, MARGEN, texto)


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
