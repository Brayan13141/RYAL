"""Importar la llegada desde el CSV del proveedor: análisis sin escribir y confirmación."""
import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction

from catalog.models import TipoArticulo
from inventario.models import Color, Par, TallaCalzado, TarifaFlete
from inventario.services.lotes import crear_lote
from inventario.validacion import cabe_en

_CENTAVOS = Decimal('0.01')
_REQUERIDAS = ('modelo', 'color', 'talla_mx', 'cantidad', 'precio_proveedor')
# "1,250" / "1,250,000" / "1,250.50": la coma separa miles; no se adivina como decimal.
_MILES_CON_COMA = re.compile(r'\d{1,3}(,\d{3})+(\.\d+)?')
_CLAVES_FALTANTES = ('modelos', 'variantes', 'colores', 'tallas', 'colores_no_asignados')


@dataclass
class RenglonAnalizado:
    modelo: str
    variante: TipoArticulo | None
    color: Color | None
    talla: TallaCalzado | None
    cantidad: int
    precio_proveedor: Decimal


@dataclass
class CambioCosto:
    variante: TipoArticulo
    costo_actual: Decimal
    costo_nuevo: Decimal


@dataclass
class Analisis:
    errores: list = field(default_factory=list)
    renglones: list = field(default_factory=list)
    faltantes: dict = field(default_factory=lambda: {k: [] for k in _CLAVES_FALTANTES})
    cambios_costo: list = field(default_factory=list)
    total_pares: int = 0
    total_proveedor: Decimal = Decimal('0')
    avisos: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errores and not any(self.faltantes.values())


def _decimal(texto):
    """Decimal finito con coma o punto decimal; None si no es un número."""
    try:
        d = Decimal((texto or '').strip().replace(',', '.'))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _parece_miles(texto):
    return bool(_MILES_CON_COMA.fullmatch((texto or '').strip()))


def _agregar(lista, valor):
    if valor not in lista:
        lista.append(valor)


def _leer(contenido, analisis):
    """Devuelve [(n_renglon, fila)] o None si el archivo no se puede leer."""
    try:
        texto = contenido.decode('utf-8-sig')
    except UnicodeDecodeError:
        analisis.errores.append('El archivo no está en UTF-8.')
        return None
    primera = next((l for l in texto.splitlines() if l.strip()), '')
    if not primera:
        analisis.errores.append('El archivo está vacío.')
        return None
    # Excel en Windows exporta con ';'; gana el separador que más aparece en el encabezado.
    delimitador = ';' if primera.count(';') > primera.count(',') else ','
    lector = csv.reader(io.StringIO(texto, newline=''), delimiter=delimitador)
    try:
        filas = list(lector)
    except csv.Error as e:
        analisis.errores.append(f'No se pudo leer el CSV: {e}')
        return None
    encabezado = None
    datos = []
    for n, fila in enumerate(filas, 1):
        if not any(c.strip() for c in fila):
            continue
        if encabezado is None:
            encabezado = [c.strip().lower() for c in fila]
            continue
        datos.append((n, fila))
    faltan = [c for c in _REQUERIDAS if c not in encabezado]
    if faltan:
        analisis.errores.append('Faltan columnas: ' + ', '.join(faltan) + '.')
        return None
    if not datos:
        analisis.errores.append('El archivo está vacío: no trae renglones de datos.')
        return None
    indices = {c: encabezado.index(c) for c in _REQUERIDAS}
    return [(n, {c: (fila[i].strip() if i < len(fila) else '') for c, i in indices.items()})
            for n, fila in datos]


def analizar_csv(contenido: bytes, *, via: str) -> Analisis:
    """Revisa el CSV contra el catálogo sin escribir nada."""
    analisis = Analisis()
    filas = _leer(contenido, analisis)
    if filas is None:
        return analisis

    colores = {}
    for c in Color.objects.all():
        colores[c.nombre] = c
        for alias in c.alias_list:
            colores.setdefault(alias, c)
    tallas = {t.mx_cm: t for t in TallaCalzado.objects.filter(activa=True)}
    bases = {}
    for b in TipoArticulo.objects.filter(padre__isnull=True):
        bases.setdefault(b.nombre.lower(), b)

    # (modelo, color, talla, precio) -> [cantidad, base, variante, color, talla]
    acumulado = {}
    cache_variante = {}
    for n, f in filas:
        malo = False
        cantidad = None
        if re.fullmatch(r'\d+', f['cantidad']) and int(f['cantidad']) > 0:
            cantidad = int(f['cantidad'])
        else:
            analisis.errores.append(
                f'Renglón {n}: la cantidad debe ser un entero mayor que cero '
                f'(trae "{f["cantidad"]}").')
            malo = True
        precio = _decimal(f['precio_proveedor'])
        if _parece_miles(f['precio_proveedor']):
            analisis.errores.append(
                f'Renglón {n}: el precio "{f["precio_proveedor"]}" parece traer separador de '
                f'miles: ¿separador de miles? escribe 1250 sin coma.')
            malo = True
        elif precio is not None and precio >= 0 and not cabe_en(
                Par._meta.get_field('precio_proveedor'), precio):
            analisis.errores.append(
                f'Renglón {n}: el precio del proveedor es demasiado grande '
                f'(trae "{f["precio_proveedor"]}").')
            malo = True
        elif precio is None or precio < 0:
            analisis.errores.append(
                f'Renglón {n}: el precio del proveedor debe ser un número no negativo '
                f'(trae "{f["precio_proveedor"]}").')
            malo = True
        talla_mx = _decimal(f['talla_mx'])
        if talla_mx is None:
            analisis.errores.append(
                f'Renglón {n}: la talla debe ser numérica (trae "{f["talla_mx"]}").')
            malo = True
        if malo:
            continue

        analisis.total_pares += cantidad
        analisis.total_proveedor += cantidad * precio

        base = bases.get(f['modelo'].lower())
        variante = None
        if base is None:
            _agregar(analisis.faltantes['modelos'], f['modelo'])
        else:
            if base.pk not in cache_variante:
                v = base.variante(via)
                cache_variante[base.pk] = v if v is not None and v.prefijo else None
            variante = cache_variante[base.pk]
            if variante is None:
                _agregar(analisis.faltantes['variantes'], base.nombre)

        color = colores.get(Color.normalizar(f['color']))
        if color is None:
            _agregar(analisis.faltantes['colores'], f['color'])
        elif base is not None and not base.colores_efectivos().filter(pk=color.pk).exists():
            _agregar(analisis.faltantes['colores_no_asignados'],
                     f'{base.nombre}: {f["color"]}')
            color = None

        talla = tallas.get(talla_mx)
        if talla is None:
            _agregar(analisis.faltantes['tallas'], f['talla_mx'])

        clave = (f['modelo'].lower(), Color.normalizar(f['color']), talla_mx, precio)
        if clave in acumulado:
            acumulado[clave][0] += cantidad
        else:
            acumulado[clave] = [cantidad, f['modelo'], variante, color, talla, precio]

    for cantidad, modelo, variante, color, talla, precio in acumulado.values():
        analisis.renglones.append(RenglonAnalizado(
            modelo=modelo, variante=variante, color=color, talla=talla,
            cantidad=cantidad, precio_proveedor=precio))

    _calcular_costos(analisis, via)
    return analisis


def _calcular_costos(analisis, via):
    """Costo nuevo por variante = mayor precio del CSV + flete; avisa si hay dos precios."""
    precios = {}
    for r in analisis.renglones:
        if r.variante is not None:
            precios.setdefault(r.variante.pk, (r.variante, set()))[1].add(r.precio_proveedor)
    if not precios:
        return
    try:
        flete = Decimal(TarifaFlete.objects.get(via=via).monto_por_par)
    except TarifaFlete.DoesNotExist:
        analisis.errores.append(f'No hay tarifa de flete para la vía {via}.')
        return
    for variante, conjunto in precios.values():
        if len(conjunto) > 1:
            lista = ', '.join(str(p.normalize()) for p in sorted(conjunto))
            analisis.avisos.append(
                f'"{variante.modelo_base.nombre}" trae precios distintos ({lista}): '
                f'el costo queda con el mayor.')
        costo_nuevo = (max(conjunto) + flete).quantize(_CENTAVOS)
        if not cabe_en(TipoArticulo._meta.get_field('costo'), costo_nuevo):
            analisis.errores.append(
                f'"{variante.modelo_base.nombre}": el costo con flete ({costo_nuevo}) '
                f'es demasiado grande.')
            continue
        analisis.cambios_costo.append(CambioCosto(
            variante=variante, costo_actual=variante.costo,
            costo_nuevo=costo_nuevo))


def confirmar_importacion(contenido: bytes, *, via: str, usuario, referencia: str = '',
                          token_sha: str | None = None):
    """Vuelve a analizar y, si todo está en orden, crea el lote y actualiza costos."""
    analisis = analizar_csv(contenido, via=via)
    if not analisis.ok:
        raise ValueError('El CSV tiene errores o faltantes; no se importó nada.')
    lineas = [{
        'tipo': r.variante, 'color': r.color, 'talla': r.talla,
        'cantidad': r.cantidad, 'precio_proveedor': r.precio_proveedor,
    } for r in analisis.renglones]
    with transaction.atomic():
        lote = crear_lote(
            via=via, origen='csv', lineas=lineas, usuario=usuario, referencia=referencia,
            token_sha=token_sha)
        for cambio in analisis.cambios_costo:
            cambio.variante.costo = cambio.costo_nuevo
            cambio.variante.save(update_fields=['costo'])
    return lote
