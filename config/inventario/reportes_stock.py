"""PDF (clientes e interno) y Excel interno del Stock, a partir de los datos de
`services/descargas.py`. El PDF de clientes nunca lleva cantidades, costos ni precios."""
import io
from functools import lru_cache

from django.contrib.staticfiles import finders
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from negocio.contabilidad_excel import _fila

FUENTE, NEGRITA = 'Helvetica', 'Helvetica-Bold'
MARGEN = 15 * mm
SIN_DATOS = 'Sin pares con estos filtros.'
MONEDA = '"$"#,##0.00'
WHATSAPP = '445 112 9186'
SITIO = 'ryalsneackers.com'

# Colores de la marca (los mismos de base.html).
NEGRO = colors.HexColor('#0A0A0A')
ORO = colors.HexColor('#D4AF37')
ORO_CLARO = colors.HexColor('#F5E7B3')
ORO_OSCURO = colors.HexColor('#8C7216')
GRIS = colors.HexColor('#5F5F5F')
GRIS_CLARO = colors.HexColor('#9A9A9A')
LINEA = colors.HexColor('#DEDBD2')
SUAVE = colors.HexColor('#F7F5EF')
ZEBRA = colors.HexColor('#FAF9F5')
BLANCO = colors.white

BANDA = 24 * mm   # alto de la banda negra del encabezado
PIE = 10 * mm     # espacio reservado para el pie de página


def _fecha(fecha):
    return fecha.strftime('%d/%m/%Y')


def _dinero(valor):
    return '' if valor is None else f'{valor:,.2f}'


TAM_TABLA, TAM_MINIMO = 7, 5
ELIPSIS = '…'


def _ajustar(texto, ancho, fuente, numerico=False, tam=TAM_TABLA, minimo=TAM_MINIMO):
    """(texto, tamaño) que cabe en `ancho` puntos: primero se achica la letra y, si aun así
    no cabe, el texto se corta con "…". Un número nunca se corta: se achica sin límite."""
    texto = str(texto)
    while stringWidth(texto, fuente, tam) > ancho and tam > minimo:
        tam -= 0.25
    if stringWidth(texto, fuente, tam) <= ancho:
        return texto, tam
    if numerico:
        return texto, max(1, tam * ancho / stringWidth(texto, fuente, tam))
    while len(texto) > 1 and stringWidth(texto + ELIPSIS, fuente, tam) > ancho:
        texto = texto[:-1]
    return texto + ELIPSIS, tam


def _envolver(prefijo, items, ancho, fuente, tam):
    """Líneas que empiezan con `prefijo` (la primera) y que no pasan de `ancho` puntos."""
    lineas, actual = [], prefijo.rstrip()
    solo_prefijo = bool(actual)
    for it in items:
        prueba = f'{actual} {it}' if solo_prefijo else f'{actual}  {it}' if actual else str(it)
        solo_prefijo = False
        if actual and stringWidth(prueba, fuente, tam) > ancho:
            lineas.append(actual)
            prueba = str(it)
        actual = prueba
    lineas.append(actual)
    return lineas


@lru_cache(maxsize=1)
def _logo():
    """El logo blanco de RYAL para la banda negra, o None si no está en los estáticos."""
    ruta = finders.find('img/logo-ryal-white.png')
    if not ruta:
        return None
    try:
        return ImageReader(ruta)
    except Exception:
        return None


def _imagen(foto):
    """ImageReader de la foto, o None si no hay foto o no se puede leer."""
    if foto is None:
        return None
    try:
        img = ImageReader(foto.path)
        img.getSize()
        return img
    except Exception:
        return None


class _Hoja:
    """Canvas con banda de marca arriba, pie con número de página y un cursor vertical."""

    def __init__(self, titulo, subtitulo, fecha, pagesize, pie):
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=pagesize, pageCompression=0)
        self.ancho, self.alto = pagesize
        self.titulo, self.subtitulo = titulo, subtitulo
        self.fecha = _fecha(fecha)
        self.pie = pie
        self.pagina = 0
        self.y = 0
        self.al_cambiar_pagina = None  # p. ej. repetir el encabezado de una tabla
        self._pagina()

    @property
    def util(self):
        return self.ancho - 2 * MARGEN

    def _pagina(self):
        c = self.c
        self.pagina += 1
        c.setFillColor(NEGRO)
        c.rect(0, self.alto - BANDA, self.ancho, BANDA, stroke=0, fill=1)
        c.setFillColor(ORO)
        c.rect(0, self.alto - BANDA - 1.2 * mm, self.ancho, 1.2 * mm, stroke=0, fill=1)
        x = MARGEN
        logo = _logo()
        if logo is not None:
            lw, lh = logo.getSize()
            alto_logo = BANDA - 7 * mm
            ancho_logo = alto_logo * lw / lh
            c.drawImage(logo, x, self.alto - BANDA + 3.5 * mm, ancho_logo, alto_logo, mask='auto')
            x += ancho_logo + 5 * mm
        else:
            c.setFillColor(ORO)
            c.setFont(NEGRITA, 18)
            c.drawString(x, self.alto - BANDA / 2 - 6, 'RYAL')
            x += stringWidth('RYAL', NEGRITA, 18) + 5 * mm
        c.setFillColor(BLANCO)
        c.setFont(NEGRITA, 17)
        c.drawString(x, self.alto - 11 * mm, self.titulo)
        c.setFillColor(ORO)
        c.setFont(FUENTE, 9)
        c.drawString(x, self.alto - 17 * mm, self.subtitulo)
        c.setFillColor(BLANCO)
        c.setFont(FUENTE, 8)
        c.drawRightString(self.ancho - MARGEN, self.alto - 11 * mm, 'Actualizado al')
        c.setFont(NEGRITA, 12)
        c.drawRightString(self.ancho - MARGEN, self.alto - 17 * mm, self.fecha)
        # Pie
        c.setStrokeColor(LINEA)
        c.setLineWidth(0.5)
        c.line(MARGEN, PIE, self.ancho - MARGEN, PIE)
        c.setFillColor(GRIS)
        c.setFont(FUENTE, 7.5)
        c.drawString(MARGEN, PIE - 4 * mm, self.pie)
        c.drawRightString(self.ancho - MARGEN, PIE - 4 * mm, f'Página {self.pagina}')
        c.setFillColor(NEGRO)
        self.y = self.alto - BANDA - 8 * mm

    def espacio(self, alto):
        """Pasa a otra página si no caben `alto` puntos más. Devuelve True si cambió."""
        if self.y - alto < PIE + 4 * mm:
            self.c.showPage()
            self._pagina()
            if self.al_cambiar_pagina:
                self.al_cambiar_pagina()
            return True
        return False

    def foto(self, foto, x, y_arriba, ancho, alto, fondo=SUAVE):
        """Foto centrada en el recuadro; sin foto (o ilegible) el recuadro dice "Sin foto"."""
        c = self.c
        c.setFillColor(fondo)
        c.roundRect(x, y_arriba - alto, ancho, alto, 2 * mm, stroke=0, fill=1)
        img = _imagen(foto)
        if img is not None:
            try:
                pad = 2 * mm
                c.drawImage(img, x + pad, y_arriba - alto + pad, ancho - 2 * pad, alto - 2 * pad,
                            preserveAspectRatio=True, anchor='c', mask='auto')
                return
            except Exception:
                pass
        c.setFillColor(GRIS_CLARO)
        c.setFont(FUENTE, 9)
        c.drawCentredString(x + ancho / 2, y_arriba - alto / 2 - 3, 'Sin foto')
        c.setFillColor(NEGRO)

    def caja(self, x, y_arriba, ancho, alto, relleno=SUAVE, barra=ORO):
        """Recuadro suave con una barra dorada a la izquierda."""
        c = self.c
        c.setFillColor(relleno)
        c.roundRect(x, y_arriba - alto, ancho, alto, 2 * mm, stroke=0, fill=1)
        if barra is not None:
            c.setFillColor(barra)
            c.rect(x, y_arriba - alto, 1.5 * mm, alto, stroke=0, fill=1)
        c.setFillColor(NEGRO)

    def vacio(self):
        self.caja(MARGEN, self.y, self.util, 16 * mm)
        self.c.setFont(NEGRITA, 12)
        self.c.drawString(MARGEN + 6 * mm, self.y - 9.5 * mm, SIN_DATOS)
        self.y -= 20 * mm

    def cerrar(self):
        self.c.save()
        return self.buf.getvalue()


# ─── PDF para clientes ──────────────────────────────────────────────────────────

COLUMNAS_CLIENTES = 2
SEP = 6 * mm
FOTO_TARJETA = 62 * mm
CHIP_ALTO, CHIP_SEP, CHIP_TAM = 7 * mm, 2 * mm, 10.5

GUIA_CLIENTES = [
    ('1.', 'Cada tarjeta es un modelo en un color. La foto es de referencia.'),
    ('2.', 'Los recuadros dorados son las tallas que tenemos hoy en tienda '
           '(talla mexicana, en centímetros).'),
    ('3.', f'Para pedir, mándanos por WhatsApp el modelo, el color y la talla: {WHATSAPP}.'),
]


def _chips(tallas, ancho):
    """Reparte las tallas en renglones de recuadros: [[(talla, ancho_chip), ...], ...]."""
    renglones, actual, usado = [], [], 0
    for t in tallas:
        w = max(10 * mm, stringWidth(str(t), NEGRITA, CHIP_TAM) + 5 * mm)
        if actual and usado + CHIP_SEP + w > ancho:
            renglones.append(actual)
            actual, usado = [], 0
        usado += (CHIP_SEP if actual else 0) + w
        actual.append((t, w))
    if actual:
        renglones.append(actual)
    return renglones


def _guia_clientes(h):
    alto = 9 * mm + len(GUIA_CLIENTES) * 5.2 * mm
    h.caja(MARGEN, h.y, h.util, alto)
    c = h.c
    c.setFont(NEGRITA, 11)
    c.drawString(MARGEN + 6 * mm, h.y - 6.5 * mm, '¿Cómo leer este catálogo?')
    for i, (num, texto) in enumerate(GUIA_CLIENTES):
        y = h.y - 12 * mm - i * 5.2 * mm
        c.setFillColor(ORO_OSCURO)
        c.setFont(NEGRITA, 9.5)
        c.drawString(MARGEN + 6 * mm, y, num)
        c.setFillColor(NEGRO)
        c.setFont(FUENTE, 9.5)
        c.drawString(MARGEN + 11 * mm, y, texto)
    h.y -= alto + 7 * mm


def _alto_tarjeta(g, ancho_interior):
    return FOTO_TARJETA + 29 * mm + len(_chips(g['tallas'], ancho_interior)) * (CHIP_ALTO + CHIP_SEP)


def _tarjeta(h, g, x, ancho, alto):
    c = h.c
    pad = 4 * mm
    c.setStrokeColor(LINEA)
    c.setLineWidth(0.8)
    c.setFillColor(BLANCO)
    c.roundRect(x, h.y - alto, ancho, alto, 3 * mm, stroke=1, fill=1)
    h.foto(g['foto'], x + pad, h.y - pad, ancho - 2 * pad, FOTO_TARJETA)
    y = h.y - pad - FOTO_TARJETA - 7 * mm
    nombre, tam = _ajustar(g['base'].nombre, ancho - 2 * pad, NEGRITA, tam=13, minimo=9)
    c.setFillColor(NEGRO)
    c.setFont(NEGRITA, tam)
    c.drawString(x + pad, y, nombre)
    y -= 5.5 * mm
    color, tam = _ajustar(f"Color: {g['color'].capitalize()}", ancho - 2 * pad, FUENTE,
                          tam=10, minimo=8)
    c.setFillColor(GRIS)
    c.setFont(FUENTE, tam)
    c.drawString(x + pad, y, color)
    y -= 6.5 * mm
    c.setFont(NEGRITA, 7.5)
    c.drawString(x + pad, y, 'TALLAS DISPONIBLES (MX)')
    y -= 2.5 * mm
    for renglon in _chips(g['tallas'], ancho - 2 * pad):
        cx = x + pad
        for talla, w in renglon:
            c.setFillColor(ORO_CLARO)
            c.setStrokeColor(ORO)
            c.setLineWidth(0.6)
            c.roundRect(cx, y - CHIP_ALTO, w, CHIP_ALTO, 1.5 * mm, stroke=1, fill=1)
            c.setFillColor(NEGRO)
            c.setFont(NEGRITA, CHIP_TAM)
            c.drawCentredString(cx + w / 2, y - CHIP_ALTO + 2.2 * mm, str(talla))
            cx += w + CHIP_SEP
        y -= CHIP_ALTO + CHIP_SEP
    c.setFillColor(NEGRO)


def pdf_clientes(grupos, fecha):
    h = _Hoja('Stock disponible', 'Tenis en existencia, listos para entrega', fecha, letter,
              f'Pedidos por WhatsApp: {WHATSAPP}   ·   {SITIO}')
    _guia_clientes(h)
    if not grupos:
        h.vacio()
        return h.cerrar()
    ancho = (h.util - SEP * (COLUMNAS_CLIENTES - 1)) / COLUMNAS_CLIENTES
    for i in range(0, len(grupos), COLUMNAS_CLIENTES):
        fila = grupos[i:i + COLUMNAS_CLIENTES]
        alto = max(_alto_tarjeta(g, ancho - 8 * mm) for g in fila)
        h.espacio(alto)
        for k, g in enumerate(fila):
            _tarjeta(h, g, MARGEN + k * (ancho + SEP), ancho, alto)
        h.y -= alto + SEP
    return h.cerrar()


# ─── PDF interno ────────────────────────────────────────────────────────────────

GUIA_INTERNO = [
    ('Aéreo / Marítimo / Nacional', 'pares que están hoy en el cuarto, según cómo llegaron.'),
    ('Total', 'todos los pares que están hoy en el cuarto (suma de las tres vías).'),
    ('En camino', 'pares ya pedidos que todavía no llegan. No cuentan en Total.'),
    ('Calidad', 'Importado = llegó por avión o barco. Nacional = de proveedor en México.'),
    ('Costo prom.', 'lo que nos costó cada par, en promedio. Ya incluye el flete.'),
    ('Valor al costo', 'lo que costaron todos los pares que hay hoy en el cuarto.'),
    ('Mayoreo / Menudeo', 'precio de venta de lista, por par.'),
    ('Otras columnas', 'precios especiales del modelo (promociones, canales).'),
]

# (encabezado, peso del ancho, es texto)
COLUMNAS_FIJAS = [('Talla', 0.7, True), ('Calidad', 1.0, True), ('Aéreo', 0.75, False),
                  ('Marítimo', 0.85, False), ('Nacional', 0.85, False), ('Total', 0.7, False),
                  ('En camino', 0.9, False), ('Costo prom.', 1.15, False),
                  ('Valor al costo', 1.3, False), ('Mayoreo', 1.1, False), ('Menudeo', 1.1, False)]
FILA_TABLA = 5.6 * mm


def _kpis(h, datos):
    t = datos['totales']
    modelos = len({f['base'].pk for f in datos['filas']})
    kpis = [('Pares en el cuarto', f"{t['total']:,}"), ('En camino', f"{t['en_camino']:,}"),
            ('Valor al costo', f"${_dinero(t['valor_costo'])}"), ('Modelos', f'{modelos:,}')]
    sep = 5 * mm
    ancho = (h.util - sep * (len(kpis) - 1)) / len(kpis)
    alto = 17 * mm
    c = h.c
    for i, (etiqueta, valor) in enumerate(kpis):
        x = MARGEN + i * (ancho + sep)
        h.caja(x, h.y, ancho, alto)
        c.setFillColor(GRIS)
        c.setFont(NEGRITA, 7.5)
        c.drawString(x + 6 * mm, h.y - 5.5 * mm, etiqueta.upper())
        texto, tam = _ajustar(valor, ancho - 9 * mm, NEGRITA, numerico=True, tam=16)
        c.setFillColor(NEGRO)
        c.setFont(NEGRITA, tam)
        c.drawString(x + 6 * mm, h.y - 13 * mm, texto)
    h.y -= alto + 6 * mm


def _guia_interno(h):
    filas = (len(GUIA_INTERNO) + 1) // 2
    alto = 10 * mm + filas * 4.6 * mm
    h.caja(MARGEN, h.y, h.util, alto)
    c = h.c
    c.setFont(NEGRITA, 10)
    c.drawString(MARGEN + 6 * mm, h.y - 6 * mm, 'Cómo leer las tablas')
    col = (h.util - 8 * mm) / 2
    for i, (termino, texto) in enumerate(GUIA_INTERNO):
        x = MARGEN + 6 * mm + (i // filas) * col
        y = h.y - 11.5 * mm - (i % filas) * 4.6 * mm
        c.setFont(NEGRITA, 8)
        c.setFillColor(NEGRO)
        c.drawString(x, y, termino + ':')
        w = stringWidth(termino + ': ', NEGRITA, 8)
        resto, tam = _ajustar(texto, col - w - 4 * mm, FUENTE, tam=8, minimo=6.5)
        c.setFont(FUENTE, tam)
        c.setFillColor(GRIS)
        c.drawString(x + w, y, resto)
    c.setFillColor(NEGRO)
    h.y -= alto + 7 * mm


class _Tabla:
    """Tabla con encabezado negro, renglones alternados y números a la derecha."""

    def __init__(self, h, columnas):
        self.h = h
        self.columnas = columnas  # [(encabezado, peso, es_texto)]
        total = sum(p for _, p, _ in columnas)
        self.anchos = [h.util * p / total for _, p, _ in columnas]
        self.zebra = False

    def encabezado(self):
        h, c = self.h, self.h.c
        c.setFillColor(NEGRO)
        c.rect(MARGEN, h.y - FILA_TABLA, h.util, FILA_TABLA, stroke=0, fill=1)
        self._celdas([e for e, _, _ in self.columnas], NEGRITA, BLANCO, todo_texto=True)
        h.y -= FILA_TABLA
        self.zebra = False

    def renglon(self, valores, fuente=FUENTE, relleno=None, color=NEGRO):
        h, c = self.h, self.h.c
        h.espacio(FILA_TABLA)
        if relleno is None:
            relleno = ZEBRA if self.zebra else BLANCO
            self.zebra = not self.zebra
        c.setFillColor(relleno)
        c.rect(MARGEN, h.y - FILA_TABLA, h.util, FILA_TABLA, stroke=0, fill=1)
        c.setStrokeColor(LINEA)
        c.setLineWidth(0.3)
        c.line(MARGEN, h.y - FILA_TABLA, MARGEN + h.util, h.y - FILA_TABLA)
        self._celdas(valores, fuente, color)
        h.y -= FILA_TABLA

    def _celdas(self, valores, fuente, color, todo_texto=False):
        c = self.h.c
        x = MARGEN
        base = self.h.y - FILA_TABLA + 1.9 * mm
        for (enc, _, es_texto), ancho, v in zip(self.columnas, self.anchos, valores):
            texto_col = es_texto or todo_texto
            v = '' if v is None else v
            apagado = not todo_texto and v in (0, '0')
            if apagado:
                v = '—'
            t, tam = _ajustar(v, ancho - 3 * mm, fuente, numerico=not texto_col and not apagado,
                              tam=7.5)
            c.setFont(fuente, tam)
            c.setFillColor(GRIS_CLARO if apagado else color)
            if texto_col:
                c.drawString(x + 1.5 * mm, base, t)
            else:
                c.drawRightString(x + ancho - 1.5 * mm, base, t)
            x += ancho
        c.setFillColor(NEGRO)


def _encabezado_modelo(h, base, foto, filas):
    alto = 26 * mm
    h.espacio(alto + 2 * FILA_TABLA + 10 * mm)
    h.caja(MARGEN, h.y, h.util, alto, barra=None)
    h.foto(foto, MARGEN + 2 * mm, h.y - 2 * mm, 22 * mm, 22 * mm, fondo=BLANCO)
    c = h.c
    x = MARGEN + 29 * mm
    c.setFillColor(NEGRO)
    c.setFont(NEGRITA, 15)
    c.drawString(x, h.y - 10 * mm, base.nombre)
    total = sum(f['total'] for f in filas)
    camino = sum(f['en_camino'] for f in filas)
    colores_n = len({f['color'] for f in filas})
    c.setFillColor(GRIS)
    c.setFont(FUENTE, 9)
    c.drawString(x, h.y - 16.5 * mm,
                 f'{total} pares en el cuarto  ·  {camino} en camino  ·  '
                 f'{colores_n} {"color" if colores_n == 1 else "colores"}')
    c.setFillColor(NEGRO)
    h.y -= alto + 4 * mm


def _tabla_color(h, color, filas, nombres):
    """Una tabla por color: la misma información que el Excel, sin repetir modelo y color."""
    columnas = COLUMNAS_FIJAS + [(n, 1.1, False) for n in nombres]
    tabla = _Tabla(h, columnas)
    h.espacio(8 * mm + 2 * FILA_TABLA)
    c = h.c
    c.setFillColor(ORO)
    c.rect(MARGEN, h.y - 3 * mm, 3 * mm, 3 * mm, stroke=0, fill=1)
    c.setFillColor(NEGRO)
    c.setFont(NEGRITA, 10.5)
    c.drawString(MARGEN + 5 * mm, h.y - 2.8 * mm, color.capitalize())
    h.y -= 5 * mm
    titulo = f'{color.capitalize()} (continúa)'

    def repetir():
        c.setFont(NEGRITA, 9)
        c.setFillColor(GRIS)
        c.drawString(MARGEN, h.y - 2.8 * mm, titulo)
        c.setFillColor(NEGRO)
        h.y -= 5 * mm
        tabla.encabezado()

    tabla.encabezado()
    h.al_cambiar_pagina = repetir
    for f in filas:
        pv = f['precios_venta']
        tabla.renglon([f['talla'], f['calidad'].capitalize(), f['por_via']['aereo'],
                       f['por_via']['maritimo'], f['por_via']['nacional'], f['total'],
                       f['en_camino'], _dinero(f['costo_promedio']), _dinero(f['valor_costo']),
                       _dinero(f['mayoreo']), _dinero(f['menudeo'])]
                      + [_dinero(pv.get(n)) for n in nombres])
    tabla.renglon(['Subtotal', '', sum(f['por_via']['aereo'] for f in filas),
                   sum(f['por_via']['maritimo'] for f in filas),
                   sum(f['por_via']['nacional'] for f in filas), sum(f['total'] for f in filas),
                   sum(f['en_camino'] for f in filas), '',
                   _dinero(sum(f['valor_costo'] for f in filas)), '', '']
                  + [''] * len(nombres), fuente=NEGRITA, relleno=ORO_CLARO)
    h.al_cambiar_pagina = None
    h.y -= 6 * mm


def _foto_modelo(base, filas):
    """La foto del modelo base; si no tiene, la del primer color que sí tenga."""
    from inventario.models import foto_de
    foto = foto_de(base)
    if foto is not None:
        return foto
    return next((f['foto'] for f in filas if f['foto'] is not None), None)


def _por_cantidad(h, items):
    h.espacio(14 * mm + 2 * FILA_TABLA)
    h.c.setFont(NEGRITA, 13)
    h.c.drawString(MARGEN, h.y - 4 * mm, 'Artículos que se cuentan por pieza')
    h.y -= 8 * mm
    tabla = _Tabla(h, [('Artículo', 4, True), ('Cantidad', 1, False), ('Costo por pieza', 1.3, False),
                       ('Valor al costo', 1.3, False)])
    tabla.encabezado()
    h.al_cambiar_pagina = tabla.encabezado
    for p in items:
        tabla.renglon([p['tipo'].nombre, p['cantidad'], _dinero(p['costo']), _dinero(p['valor'])])
    h.al_cambiar_pagina = None
    h.y -= 6 * mm


def pdf_interno(datos, fecha):
    h = _Hoja('Stock interno', 'Solo para el equipo — no compartir con clientes', fecha,
              landscape(letter), f'RYAL · Documento interno · {SITIO}')
    filas = datos['filas']
    nombres = datos['nombres_precio_venta']
    if not filas and not datos['por_cantidad']:
        h.vacio()
        return h.cerrar()
    if filas:
        _kpis(h, datos)
    _guia_interno(h)
    bases = {}
    for f in filas:
        bases.setdefault(f['base'].pk, []).append(f)
    for grupo in bases.values():
        base = grupo[0]['base']
        _encabezado_modelo(h, base, _foto_modelo(base, grupo), grupo)
        por_color = {}
        for f in grupo:
            por_color.setdefault(f['color'], []).append(f)
        for color, de_color in por_color.items():
            _tabla_color(h, color, de_color, nombres)
    if datos['por_cantidad']:
        _por_cantidad(h, datos['por_cantidad'])
    if filas:
        t = datos['totales']
        h.espacio(16 * mm)
        h.caja(MARGEN, h.y, h.util, 13 * mm, relleno=NEGRO, barra=ORO)
        c = h.c
        c.setFillColor(ORO)
        c.setFont(NEGRITA, 9)
        c.drawString(MARGEN + 6 * mm, h.y - 8 * mm, 'TOTAL GENERAL')
        c.setFillColor(BLANCO)
        c.setFont(NEGRITA, 11)
        c.drawString(MARGEN + 40 * mm, h.y - 8 * mm,
                     f"{t['total']:,} pares en el cuarto   ·   {t['en_camino']:,} en camino   ·   "
                     f"valor al costo ${_dinero(t['valor_costo'])}")
        c.setFillColor(NEGRO)
        h.y -= 17 * mm
    return h.cerrar()


def excel_interno(datos, fecha):
    nombres = datos['nombres_precio_venta']
    wb = Workbook()
    ws = wb.active
    ws.title = 'Stock'
    cols = (['Modelo', 'Color', 'Talla', 'Calidad', 'Aéreo', 'Marítimo', 'Nacional', 'Total',
             'En camino', 'Costo promedio', 'Mayoreo', 'Menudeo', 'Valor al costo'] + nombres)
    _fila(ws, cols)
    for celda in ws[1]:
        celda.font = Font(bold=True)
    for f in datos['filas']:
        pv = f['precios_venta']
        _fila(ws, [f['base'].nombre, f['color'], f['talla'], f['calidad'],
                   f['por_via']['aereo'], f['por_via']['maritimo'], f['por_via']['nacional'],
                   f['total'], f['en_camino'], f['costo_promedio'], f['mayoreo'], f['menudeo'],
                   f['valor_costo']] + [pv.get(n) for n in nombres])
    t = datos['totales']
    _fila(ws, ['Total general', None, None, None, None, None, None, t['total'], t['en_camino'],
               None, None, None, t['valor_costo']] + [None] * len(nombres))
    for celda in ws[ws.max_row]:
        celda.font = Font(bold=True)
    for fila in ws.iter_rows(min_row=2):
        for i in [10, 11, 12, 13] + list(range(14, 14 + len(nombres))):
            fila[i - 1].number_format = MONEDA
    for i, nombre in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, len(nombre) + 2)

    wc = wb.create_sheet('Por cantidad')
    wc.append(['Artículo', 'Cantidad', 'Costo', 'Valor'])
    for celda in wc[1]:
        celda.font = Font(bold=True)
    for p in datos['por_cantidad']:
        _fila(wc, [p['tipo'].nombre, p['cantidad'], p['costo'], p['valor']])
    for fila in wc.iter_rows(min_row=2):
        fila[2].number_format = fila[3].number_format = MONEDA
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
