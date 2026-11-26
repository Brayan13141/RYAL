"""PDF (clientes e interno) y Excel interno del Stock, a partir de los datos de
`services/descargas.py`. El PDF de clientes nunca lleva cantidades, costos ni precios."""
import io

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from negocio.contabilidad_excel import _fila

FUENTE, NEGRITA = 'Helvetica', 'Helvetica-Bold'
FOTO = 30 * mm
MARGEN = 15 * mm
SIN_DATOS = 'Sin pares con estos filtros.'
MONEDA = '"$"#,##0.00'


def _fecha(fecha):
    return fecha.strftime('%d/%m/%Y')


def _dinero(valor):
    return '' if valor is None else f'{valor:,.2f}'


class _Hoja:
    """Canvas con encabezado en cada página y un cursor vertical que baja."""

    def __init__(self, titulo, fecha, pagesize):
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=pagesize, pageCompression=0)
        self.ancho, self.alto = pagesize
        self.titulo = f'RYAL — {titulo}  {_fecha(fecha)}'
        self.y = 0
        self._pagina()

    def _pagina(self):
        self.c.setFont(NEGRITA, 14)
        self.c.setFillColor(colors.black)
        self.c.drawString(MARGEN, self.alto - MARGEN, self.titulo)
        self.y = self.alto - MARGEN - 10 * mm

    def espacio(self, alto):
        """Pasa a otra página si no caben `alto` puntos más."""
        if self.y - alto < MARGEN:
            self.c.showPage()
            self._pagina()

    def foto(self, foto, x, y_arriba):
        """Foto de ~30 mm de lado; sin foto (o ilegible) un recuadro gris con "Sin foto"."""
        c = self.c
        try:
            if foto is None:
                raise ValueError('sin foto')
            c.drawImage(ImageReader(foto.path), x, y_arriba - FOTO, FOTO, FOTO,
                        preserveAspectRatio=True, anchor='c')
            return
        except Exception:
            pass
        c.setFillColor(colors.lightgrey)
        c.rect(x, y_arriba - FOTO, FOTO, FOTO, stroke=0, fill=1)
        c.setFillColor(colors.dimgrey)
        c.setFont(FUENTE, 9)
        c.drawCentredString(x + FOTO / 2, y_arriba - FOTO / 2 - 3, 'Sin foto')
        c.setFillColor(colors.black)

    def vacio(self):
        self.c.setFont(FUENTE, 12)
        self.c.drawString(MARGEN, self.y, SIN_DATOS)

    def cerrar(self):
        self.c.save()
        return self.buf.getvalue()


def pdf_clientes(grupos, fecha):
    h = _Hoja('Stock disponible', fecha, letter)
    if not grupos:
        h.vacio()
    for g in grupos:
        h.espacio(FOTO + 4 * mm)
        h.foto(g['foto'], MARGEN, h.y)
        x = MARGEN + FOTO + 6 * mm
        h.c.setFont(NEGRITA, 13)
        h.c.drawString(x, h.y - 12 * mm, f"{g['base'].nombre} · {g['color'].capitalize()}")
        h.c.setFont(FUENTE, 11)
        h.c.drawString(x, h.y - 19 * mm, 'Tallas: ' + '  '.join(g['tallas']))
        h.y -= FOTO + 4 * mm
    return h.cerrar()


def _tabla_color(h, color, filas, nombres):
    """Una tabla por color: la misma información que el Excel, sin repetir modelo y color."""
    cols = (['Talla', 'Calidad', 'Aéreo', 'Marít.', 'Nac.', 'Total', 'Camino',
             'Costo prom.', 'Valor costo', 'Mayoreo', 'Menudeo'] + nombres)
    ancho = (h.ancho - 2 * MARGEN) / len(cols)
    h.espacio(14 * mm)
    h.c.setFont(NEGRITA, 10)
    h.c.drawString(MARGEN, h.y, color.capitalize())
    h.y -= 5 * mm

    def renglon(valores, fuente):
        h.c.setFont(fuente, 7)
        for i, v in enumerate(valores):
            h.c.drawString(MARGEN + i * ancho, h.y, str(v)[:int(ancho / 3.6)])
        h.y -= 4 * mm

    renglon(cols, NEGRITA)
    for f in filas:
        h.espacio(6 * mm)
        pv = f['precios_venta']
        renglon([f['talla'], f['calidad'], f['por_via']['aereo'], f['por_via']['maritimo'],
                 f['por_via']['nacional'], f['total'], f['en_camino'],
                 _dinero(f['costo_promedio']), _dinero(f['valor_costo']),
                 _dinero(f['mayoreo']), _dinero(f['menudeo'])]
                + [_dinero(pv.get(n)) for n in nombres], FUENTE)
    h.y -= 2 * mm


def pdf_interno(datos, fecha):
    h = _Hoja('Stock interno', fecha, landscape(letter))
    filas = datos['filas']
    if not filas and not datos['por_cantidad']:
        h.vacio()
    nombres = datos['nombres_precio_venta']
    bases = {}
    for f in filas:
        bases.setdefault(f['base'].pk, []).append(f)
    for grupo in bases.values():
        h.espacio(FOTO + 4 * mm)
        h.foto(grupo[0]['foto'], MARGEN, h.y)
        h.c.setFont(NEGRITA, 13)
        h.c.drawString(MARGEN + FOTO + 6 * mm, h.y - 12 * mm, grupo[0]['base'].nombre)
        h.y -= FOTO + 4 * mm
        por_color = {}
        for f in grupo:
            por_color.setdefault(f['color'], []).append(f)
        for color, de_color in por_color.items():
            _tabla_color(h, color, de_color, nombres)
    if datos['por_cantidad']:
        h.espacio(14 * mm)
        h.c.setFont(NEGRITA, 11)
        h.c.drawString(MARGEN, h.y, 'Por cantidad')
        h.y -= 5 * mm
        for p in datos['por_cantidad']:
            h.espacio(6 * mm)
            h.c.setFont(FUENTE, 8)
            h.c.drawString(MARGEN, h.y, f"{p['tipo'].nombre}   {p['cantidad']}   "
                           f"costo {_dinero(p['costo'])}   valor {_dinero(p['valor'])}")
            h.y -= 4 * mm
    if filas:
        t = datos['totales']
        h.espacio(10 * mm)
        h.c.setFont(NEGRITA, 9)
        h.c.drawString(MARGEN, h.y, f"Total general: {t['total']} pares, {t['en_camino']} en camino, "
                       f"valor al costo {_dinero(t['valor_costo'])}")
    return h.cerrar()


def excel_interno(datos, fecha):
    nombres = datos['nombres_precio_venta']
    wb = Workbook()
    ws = wb.active
    ws.title = 'Stock'
    cols = (['Modelo', 'Color', 'Talla', 'Calidad', 'Aéreo', 'Marítimo', 'Nacional', 'Total',
             'En camino', 'Costo promedio', 'Mayoreo', 'Menudeo', 'Valor al costo'] + nombres)
    ws.append(cols)
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
