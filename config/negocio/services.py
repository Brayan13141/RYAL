import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F

from catalog.models import Product, TipoArticulo
from .models import Pedido, PedidoItem, Pago, Cliente


class VentaInvalida(Exception):
    """Payload de venta inválido (SKU inexistente/inactivo, cantidad o precio inválidos)."""


class VentaSinTipo(VentaInvalida):
    """Una o más líneas no resuelven a ningún TipoArticulo: la venta NO se graba.

    Hereda de VentaInvalida para que los `except VentaInvalida` existentes la
    sigan atrapando, pero quien quiera las sugerencias tiene que atraparla
    ANTES — el orden de los except importa.
    """

    def __init__(self, detalles):
        self.detalles = detalles
        textos = ', '.join(f'«{d["texto"]}»' for d in detalles)
        super().__init__(f'Sin tipo: {textos}. La venta no se registró.')


_METODOS_VALIDOS = {m[0] for m in Pago.METODO_CHOICES}


def _parse_cantidad(valor):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise VentaInvalida(f'Cantidad inválida: {valor!r}')
    if n <= 0:
        raise VentaInvalida(f'La cantidad debe ser > 0: {valor!r}')
    return n


def _parse_precio(valor):
    try:
        precio = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise VentaInvalida(f'Precio inválido: {valor!r}')
    if precio < 0:
        raise VentaInvalida(f'El precio no puede ser negativo: {valor!r}')
    return precio


MOSTRADOR_TELEFONO = 'TIENDA-MOSTRADOR'


def cargar_aliases():
    """`{texto normalizado: TipoArticulo}` — el mapa que espera `resolver_tipo`."""
    from catalog.models import AliasTexto
    return {a.texto: a.tipo for a in AliasTexto.objects.select_related('tipo')}


def resolver_tipo(texto, tipos, aliases):
    """Alias exacto primero, después la keyword MÁS LARGA (la más específica).

    Es la ÚNICA regla del sistema: la usan el camino que graba el costo de una
    venta, los reportes y el lookup del bot. Antes eran dos —la venta se
    quedaba con el primer tipo en orden alfabético (`Meta.ordering =
    ['nombre']`) y el reporte con la keyword más larga—, y esa divergencia
    grabó 4 pares de New Balance con costo de gorra: `'new'` (de `Gorras New
    Era`) está contenido en `'new balance'` y la G le ganaba a la T. Nada lo
    delataba: la venta se registra sin error y el dashboard suma bien lo que
    tiene grabado.

    Si algún día hace falta otra regla, que sea un parámetro de ESTA función y
    no una segunda copia. Dos formas de calcular lo mismo es la firma del
    problema.

    El alias va adelante porque es una decisión humana explícita para ese
    texto exacto; la keyword es una regla general.
    """
    return resolver_tipo_con_origen(texto, tipos, aliases)[0]


def resolver_tipo_con_origen(texto, tipos, aliases):
    """Igual que `resolver_tipo`, pero ademas dice DE DONDE salio el tipo:
    devuelve `(tipo, origen)` con origen `'alias'`, `'keyword'` o `None`.

    Es la misma regla, no una copia — `resolver_tipo` delega aca. El origen
    hace falta para verificar un alias recien creado: un texto que cae en el
    tipo correcto de casualidad, por keyword, se ve identico a uno asignado a
    mano, y esa ambiguedad es justo lo que hace que un alias parezca guardado
    sin estarlo.
    """
    from catalog.services import buscar_tipo_articulo, normalizar_texto
    alias_tipo = aliases.get(normalizar_texto(texto))
    if alias_tipo is not None:
        return alias_tipo, 'alias'
    tipo = buscar_tipo_articulo(texto, tipos=tipos)
    return (tipo, 'keyword') if tipo is not None else (None, None)


@transaction.atomic
def crear_pedido_tienda_bot(*, items, envio=Decimal('0')):
    """Crea un pedido de tienda física via bot. Sin SKU, sin normalize_telefono.

    `items`: lista de {description, price, qty}
    """
    if not items:
        raise VentaInvalida('La venta no tiene ítems.')

    from catalog.services import sugerencias_de_tipo

    tipos = list(TipoArticulo.objects.all())
    aliases = cargar_aliases()

    # Resolver TODO antes de crear nada. Registrar con costo $0 lo que no
    # matchea es lo que dejó $14,710 con 100% de margen declarado: cero es un
    # costo plausible, así que después nada lo delata. Sin tipo no hay venta.
    resueltos = []
    faltantes = []
    for item in items:
        nombre_snap = (str(item.get('description') or '').strip() or 'ítem tienda')[:200]
        qty = int(item['qty'])
        precio_u = Decimal(str(item['price']))
        tipo = resolver_tipo(nombre_snap, tipos, aliases)
        if tipo is None and not any(f['texto'] == nombre_snap for f in faltantes):
            faltantes.append({
                'texto': nombre_snap,
                'qty': qty,
                'precio': float(precio_u),
                'sugerencias': [
                    {'tipo_id': t.pk, 'nombre': t.nombre, 'costo': float(t.costo)}
                    for t in sugerencias_de_tipo(nombre_snap, tipos=tipos)
                ],
            })
        resueltos.append((nombre_snap, qty, precio_u, tipo))

    if faltantes:
        raise VentaSinTipo(faltantes)

    precio_total = sum(precio * qty for _, qty, precio, _ in resueltos)
    partes_desc = []

    # El cliente mostrador se resuelve DESPUES del rechazo: asi "una venta sin
    # tipo no crea nada" es cierto por el orden del codigo y no por el rollback
    # de @transaction.atomic, que alguien podria quitar mas adelante.
    cliente, _ = Cliente.objects.get_or_create(
        telefono=MOSTRADOR_TELEFONO,
        defaults={'nombre': 'Mostrador'},
    )

    pedido = Pedido.objects.create(
        cliente=cliente,
        descripcion='',
        costo_producto=Decimal('0'),
        precio_venta=precio_total,
        envio=Decimal(str(envio)),
        estado=Pedido.PAGADO,
        origen=Pedido.TIENDA,
    )

    total_costo = Decimal('0')
    for nombre_snap, qty, precio_u, tipo in resueltos:
        PedidoItem.objects.create(
            pedido=pedido,
            product=None,
            sku_snapshot='TIENDA-BOT',
            nombre_snapshot=nombre_snap,
            cantidad=qty,
            costo_unitario=tipo.costo,
            precio_unitario=precio_u,
        )
        total_costo += tipo.costo * qty
        partes_desc.append(f'{nombre_snap[:30]} ×{qty}')

    pedido.costo_producto = total_costo
    pedido.descripcion = f'{len(resueltos)} art.: ' + ', '.join(partes_desc)
    pedido.save(update_fields=['costo_producto', 'descripcion'])

    Pago.objects.create(
        pedido=pedido,
        fecha=datetime.date.today(),
        monto=precio_total,
        metodo_pago=Pago.EFECTIVO,
    )
    # Ya no puede haber líneas sin tipo: la venta se habría rechazado. El
    # campo se conserva para no romper al bot desplegado, que lo lee.
    pedido.sin_tipo = []
    return pedido


@transaction.atomic
def crear_venta_tienda(*, lineas, cliente=None, metodo_pago='efectivo'):
    """Crea una venta de tienda física: Pedido PAGADO + PedidoItem + Pago completo.

    `lineas`: lista de dicts {sku, cantidad, precio_unitario}. El precio es editable
    (override del cajero); el COSTO se toma siempre de Product.effective_base_price en el
    servidor (nunca se confía al cliente) para preservar la integridad de la ganancia.
    Lanza VentaInvalida si algo no valida; la transacción atómica garantiza que no se
    cree nada parcial.
    """
    if not lineas:
        raise VentaInvalida('La venta no tiene líneas.')
    if metodo_pago not in _METODOS_VALIDOS:
        raise VentaInvalida(f'Método de pago inválido: {metodo_pago!r}')

    pedido = Pedido.objects.create(
        cliente=cliente,
        descripcion='',
        costo_producto=Decimal('0'),
        precio_venta=Decimal('0'),
        envio=Decimal('0'),
        estado=Pedido.PAGADO,
        origen=Pedido.TIENDA,
    )

    total_precio = Decimal('0')
    total_costo = Decimal('0')
    partes_desc = []

    for linea in lineas:
        sku = (linea.get('sku') or '').strip()
        if not sku:
            raise VentaInvalida('Línea sin SKU.')
        try:
            product = Product.objects.get(sku=sku, is_active=True)
        except Product.DoesNotExist:
            raise VentaInvalida(f'Producto no encontrado o inactivo: {sku}')

        cantidad = _parse_cantidad(linea.get('cantidad'))
        precio_unitario = _parse_precio(linea.get('precio_unitario'))
        costo_unitario = product.effective_base_price  # SIEMPRE del servidor (respeta override de subcategoría)

        PedidoItem.objects.create(
            pedido=pedido, product=product,
            sku_snapshot=product.sku, nombre_snapshot=product.name,
            cantidad=cantidad,
            costo_unitario=costo_unitario, precio_unitario=precio_unitario,
        )
        total_precio += precio_unitario * cantidad
        total_costo += costo_unitario * cantidad
        partes_desc.append(f'{product.name} ×{cantidad}')

    pedido.precio_venta = total_precio
    pedido.costo_producto = total_costo
    pedido.descripcion = f'{len(lineas)} art.: ' + ', '.join(partes_desc)
    pedido.save(update_fields=['precio_venta', 'costo_producto', 'descripcion'])

    Pago.objects.create(
        pedido=pedido, fecha=datetime.date.today(),
        monto=total_precio, metodo_pago=metodo_pago,
    )
    return pedido


@transaction.atomic
def crear_pedido_bot(*, nombre, telefono, items, envio=Decimal('0'),
                     descuento_aplicado=Decimal('0'), codigo_descuento_id=None):
    """Crea un pedido vía bot WhatsApp. Cada item puede incluir 'costo' opcional."""
    from .phone import normalize_telefono
    if not items:
        raise VentaInvalida('La sesión no tiene ítems.')
    telefono_norm = normalize_telefono(telefono)
    cliente, _ = Cliente.objects.get_or_create(
        telefono=telefono_norm, defaults={'nombre': nombre}
    )
    envio_d = _parse_precio(envio)
    descuento_d = _parse_precio(descuento_aplicado)

    codigo_obj = None
    if codigo_descuento_id:
        from catalog.models import CodigoDescuento
        from catalog.services import consumir_uso
        import datetime
        try:
            codigo_obj = CodigoDescuento.objects.get(pk=codigo_descuento_id)
            # Re-validate: code must still be active, not expired, not exhausted
            if not codigo_obj.is_active:
                codigo_obj = None
            elif codigo_obj.valid_hasta and codigo_obj.valid_hasta < datetime.date.today():
                codigo_obj = None
            # Chequeo de usos_max + incremento en un solo UPDATE atómico.
            # Si el pedido falla más abajo, transaction.atomic lo revierte.
            elif not consumir_uso(pk=codigo_obj.pk):
                codigo_obj = None
        except CodigoDescuento.DoesNotExist:
            codigo_obj = None
    if codigo_descuento_id and codigo_obj is None:
        descuento_d = Decimal('0')

    pedido = Pedido.objects.create(
        cliente=cliente,
        descripcion='',
        costo_producto=Decimal('0'),
        precio_venta=Decimal('0'),
        envio=envio_d,
        descuento_aplicado=descuento_d,
        codigo_descuento=codigo_obj,
        estado=Pedido.PAGADO,
        origen=Pedido.BOT,
    )
    total_precio = Decimal('0')
    total_costo = Decimal('0')
    partes_desc = []

    for item in items:
        precio = _parse_precio(item.get('price', 0))
        qty = _parse_cantidad(item.get('qty', 1))
        costo = _parse_precio(item.get('costo', 0))
        nombre_snap = str(item.get('description', ''))[:200]
        PedidoItem.objects.create(
            pedido=pedido,
            product=None,
            sku_snapshot='BOT',
            nombre_snapshot=nombre_snap,
            cantidad=qty,
            costo_unitario=costo,
            precio_unitario=precio,
        )
        total_precio += precio * qty
        total_costo += costo * qty
        partes_desc.append(f'{nombre_snap[:30]} ×{qty}')

    pedido.precio_venta = total_precio
    pedido.costo_producto = total_costo
    pedido.descripcion = f'Bot: {", ".join(partes_desc)}'
    # El descuento nunca excede el total a cobrar (precio + envío) — el bot ya
    # floorea el total mostrado en WhatsApp, esto evita registrarlo negativo.
    pedido.descuento_aplicado = min(descuento_d, total_precio + envio_d)
    pedido.save(update_fields=[
        'precio_venta', 'costo_producto', 'descripcion', 'descuento_aplicado',
    ])

    return pedido


def ranking_por_tipo(fecha_ini=None, fecha_fin=None):
    """Ventas del negocio agrupadas por TipoArticulo, más vendido primero.

    En el negocio no existe la entidad "producto": ninguna línea tiene FK a
    catalog.Product y el nombre es texto libre que el empleado tecleó en el
    grupo del bot ('gorra', 'gorras' y 'gorra barbas' son lo mismo). El tipo
    es la única agrupación confiable, y además ya es de grano producto.

    El dinero sale del `precio_unitario`/`costo_unitario` GRABADOS en cada
    línea, no de recalcular contra el catálogo: son ventas cerradas y su
    precio de ese día es el que vale.

    Devuelve una lista de dicts con tipo (None = sin clasificar), piezas,
    ingreso, costo, ganancia, sin_desglose y `detalle`: el desglose de los
    textos tal como se teclearon, que es lo único que se puede corregir con
    una keyword o un alias. Sin clasificar va siempre al final: esconderlo
    descuadraría los totales sin que se note.
    """
    pedidos = Pedido.objects.filter(estado=Pedido.PAGADO)
    if fecha_ini is not None:
        pedidos = pedidos.filter(fecha__gte=fecha_ini)
    if fecha_fin is not None:
        pedidos = pedidos.filter(fecha__lt=fecha_fin)

    tipos = list(TipoArticulo.objects.all())
    aliases = cargar_aliases()
    acumulado = {}

    def fila_de(tipo):
        nombre = tipo.nombre if tipo else None
        if nombre not in acumulado:
            acumulado[nombre] = {
                'tipo': nombre, 'piezas': 0, 'ingreso': Decimal('0'),
                'costo': Decimal('0'), 'sin_desglose': 0, 'detalle': {},
            }
        return acumulado[nombre]

    def anotar(fila, texto, piezas, ingreso, costo, sin_desglose=0):
        """Suma en la fila y en su desglose por texto de un solo lado.

        El desglose se acumula acá y no en un segundo recorrido a propósito:
        así no puede despegarse de la fila que resume, porque es la misma
        suma. El texto entra TAL COMO SE TECLEÓ — `playera g5` y `playera G5`
        son dos formas distintas de escribirlo, y verlas separadas es justo lo
        que deja elegir la keyword que las arregla.
        """
        fila['piezas'] += piezas
        fila['ingreso'] += ingreso
        fila['costo'] += costo
        fila['sin_desglose'] += sin_desglose

        linea = fila['detalle'].setdefault(texto, {
            'texto': texto, 'piezas': 0, 'ingreso': Decimal('0'),
            'costo': Decimal('0'), 'sin_desglose': 0,
        })
        linea['piezas'] += piezas
        linea['ingreso'] += ingreso
        linea['costo'] += costo
        linea['sin_desglose'] += sin_desglose

    for pedido in pedidos.prefetch_related('items'):
        items = list(pedido.items.all())

        if not items:
            # Pedido capturado a mano: no hay campo de cantidad, la cantidad
            # vive dentro del texto. Cuenta 1 pieza y se declara en pantalla;
            # parsear prosa para afinar eso cambia un error chico por un
            # riesgo permanente.
            fila = fila_de(resolver_tipo(pedido.descripcion, tipos, aliases))
            anotar(
                fila, pedido.descripcion, 1,
                pedido.precio_venta - pedido.descuento_aplicado,
                pedido.costo_producto, sin_desglose=1,
            )
            continue

        # El descuento vive en el pedido, no en la línea: se reparte a
        # prorrata para que la suma del ranking siga siendo la del dashboard.
        bruto = sum(i.precio_unitario * i.cantidad for i in items)
        repartido = Decimal('0')
        for pos, item in enumerate(items):
            subtotal = item.precio_unitario * item.cantidad
            if pedido.descuento_aplicado and bruto:
                if pos == len(items) - 1:
                    parte = pedido.descuento_aplicado - repartido
                else:
                    parte = (pedido.descuento_aplicado * subtotal / bruto).quantize(
                        Decimal('0.01'))
                    repartido += parte
            else:
                parte = Decimal('0')

            fila = fila_de(resolver_tipo(item.nombre_snapshot, tipos, aliases))
            anotar(
                fila, item.nombre_snapshot, item.cantidad,
                subtotal - parte, item.costo_unitario * item.cantidad,
            )

    filas = []
    for fila in acumulado.values():
        fila['ganancia'] = fila['ingreso'] - fila['costo']
        detalle = []
        for linea in fila['detalle'].values():
            linea['ganancia'] = linea['ingreso'] - linea['costo']
            detalle.append(linea)
        fila['detalle'] = sorted(detalle, key=lambda d: (-d['piezas'], -d['ingreso']))
        filas.append(fila)

    return ordenar_ranking(filas, 'piezas')


def serie_piezas_por_tipo(year, month, meses=6):
    """Piezas por tipo, mes a mes, para los `meses` que terminan en (year, month).

    Devuelve `(etiquetas, {tipo: [piezas]})`, con el mes pedido al final y
    `tipo` None para la fila sin clasificar, igual que `ranking_por_tipo`. Un
    mes sin ventas de ese tipo vale 0 y NO se salta: la forma de la serie es
    el dato, y una serie con huecos la deformaría.

    Corre la misma agregación una vez por mes en lugar de estrenar una query
    agrupada: son ~125 líneas en total, y dos formas de calcular lo mismo es
    exactamente cómo se despegan los números de una pantalla de la otra.
    """
    from .utils import _mes_range, _MESES_ES

    etiquetas, series = [], {}
    for pos in range(meses):
        corrido = (year * 12 + month - 1) - (meses - 1 - pos)
        y, m = corrido // 12, corrido % 12 + 1
        etiquetas.append(_MESES_ES[m - 1])

        ini, fin = _mes_range(y, m)
        for fila in ranking_por_tipo(ini, fin):
            series.setdefault(fila['tipo'], [0] * meses)[pos] = fila['piezas']

    return etiquetas, series


def auditar_textos():
    """Textos de venta que SI resuelven, con lo que la regla dice hoy.

    Es el complemento de `textos_sin_tipo()`, que solo caza los textos que no
    matchean NADA. El que matchea al tipo EQUIVOCADO no falla, no avisa y el
    dashboard suma bien lo que tiene grabado — asi `new balance` cargo el
    costo de una gorra durante meses sin que nada lo delatara. Aca se ve.

    Sirve para dos cosas: verificar que un alias recien creado resuelve como
    creias (por eso viene el `origen`), y detectar textos mal ruteados antes
    de que se acumulen.

    SOLO LECTURA — no escribe ni una fila. `costo_unitario` es una columna
    guardada y no se re-costea: es una decision de negocio ya tomada.
    """
    tipos = list(TipoArticulo.objects.all())
    aliases = cargar_aliases()
    acumulado = {}

    for pedido in Pedido.objects.filter(estado=Pedido.PAGADO).prefetch_related('items'):
        for item in pedido.items.all():
            texto = (item.nombre_snapshot or '').strip()
            if not texto:
                continue
            tipo, origen = resolver_tipo_con_origen(texto, tipos, aliases)
            if tipo is None:
                # Ya los lista `textos_sin_tipo()`. Repetirlos aca seria una
                # segunda forma de calcular lo mismo, que es justo el patron
                # que dejo 4 New Balance con costo de gorra.
                continue
            fila = acumulado.setdefault(texto, {
                'texto': texto, 'tipo': tipo, 'origen': origen,
                'costo_regla': tipo.costo, 'piezas': 0,
                'pedidos': set(), 'costos_grabados': set(),
            })
            fila['piezas'] += item.cantidad
            fila['pedidos'].add(pedido.pk)
            fila['costos_grabados'].add(item.costo_unitario)

    filas = []
    for fila in acumulado.values():
        fila['pedidos'] = len(fila['pedidos'])
        fila['costos_grabados'] = sorted(fila['costos_grabados'])
        fila['coincide'] = fila['costos_grabados'] == [fila['costo_regla']]
        filas.append(fila)

    # Divergentes primero; dentro de cada grupo, lo que mas piezas mueve.
    filas.sort(key=lambda f: (f['coincide'], -f['piezas'], f['texto']))
    return filas


def textos_sin_tipo():
    """Textos de venta que hoy no matchean ningún TipoArticulo.

    Hoy una venta sin tipo se RECHAZA (`VentaSinTipo`), así que esta lista es
    histórica: son las que entraron cuando el costo se resolvía con
    `next((...), Decimal('0'))` y la venta quedaba grabada con costo CERO, o
    sea 100% de margen. No fallaba, no avisaba y no dejaba rastro: el único
    síntoma es que la ganancia iguala al ingreso, y son $14,710 así. Sirve
    para saber qué keyword hay que nombrar y qué costo hay que corregir a
    mano — agregar la keyword ahora NO repara lo ya grabado.

    Ordena por ingreso: lo que más dinero mueve es lo que más urge nombrar.
    """
    tipos = list(TipoArticulo.objects.all())
    aliases = cargar_aliases()
    acumulado = {}

    def fila_de(texto):
        if texto not in acumulado:
            acumulado[texto] = {
                'texto': texto, 'piezas': 0, 'ingreso': Decimal('0'),
                'costo': Decimal('0'), 'pedidos': set(),
            }
        return acumulado[texto]

    for pedido in Pedido.objects.filter(estado=Pedido.PAGADO).prefetch_related('items'):
        items = list(pedido.items.all())

        if not items:
            texto = (pedido.descripcion or '').strip()
            if not texto or resolver_tipo(texto, tipos, aliases):
                continue
            fila = fila_de(texto)
            fila['piezas'] += 1
            fila['ingreso'] += pedido.precio_venta - pedido.descuento_aplicado
            fila['costo'] += pedido.costo_producto
            fila['pedidos'].add(pedido.pk)
            continue

        for item in items:
            texto = (item.nombre_snapshot or '').strip()
            if not texto or resolver_tipo(texto, tipos, aliases):
                continue
            fila = fila_de(texto)
            fila['piezas'] += item.cantidad
            fila['ingreso'] += item.precio_unitario * item.cantidad
            fila['costo'] += item.costo_unitario * item.cantidad
            fila['pedidos'].add(pedido.pk)

    filas = []
    for fila in acumulado.values():
        fila['pedidos'] = len(fila['pedidos'])
        # Sin costo grabado la venta entera se contó como ganancia. Es el caso
        # grave y merece señalarse aparte de "falta una keyword".
        fila['costo_cero'] = fila['costo'] == 0
        filas.append(fila)

    filas.sort(key=lambda f: (-f['ingreso'], f['texto']))
    return filas


def _textos_de_venta():
    """Todos los textos distintos con que se registraron ventas cobradas."""
    from django.db.models import Count

    textos = set(
        PedidoItem.objects
        .filter(pedido__estado=Pedido.PAGADO)
        .values_list('nombre_snapshot', flat=True)
    )
    textos |= set(
        Pedido.objects
        .filter(estado=Pedido.PAGADO)
        .annotate(_n=Count('items')).filter(_n=0)
        .values_list('descripcion', flat=True)
    )
    return {t.strip() for t in textos if t and t.strip()}


def conflictos_de_keyword(tipo, keyword):
    """Textos que hoy resuelven bien y se romperían si `tipo` sumara `keyword`.

    `matches()` es por substring, así que una keyword corta se lleva puesto
    todo lo que la contenga: 'new' (de Gorras New Era) se quedó con
    'new balance'. Simular antes de escribir es lo único que lo impide.

    Se simula con `resolver_tipo`, la misma regla que va a grabar el costo —
    antes se probaban dos reglas distintas porque el bot y el reporte no
    coincidían. Los alias entran en la simulación: a un texto con alias no lo
    puede romper ninguna keyword, así que reportarlo sería ruido.
    """
    tipos = list(TipoArticulo.objects.all())
    aliases = cargar_aliases()
    simulados = [
        TipoArticulo(pk=t.pk, nombre=t.nombre, costo=t.costo,
                     keywords=f'{t.keywords},{keyword}' if t.pk == tipo.pk else t.keywords)
        for t in tipos
    ]
    # El orden sigue importando: ante dos keywords que matchean con la MISMA
    # longitud, `buscar_tipo_articulo` se queda con la primera que ve.
    simulados.sort(key=lambda t: t.nombre)

    conflictos = []
    for texto in sorted(_textos_de_venta()):
        antes = resolver_tipo(texto, tipos, aliases)
        if antes is None:
            continue
        despues = resolver_tipo(texto, simulados, aliases)
        if despues is None or despues.pk != antes.pk:
            conflictos.append({
                'texto': texto,
                'antes': antes.nombre,
                'despues': despues.nombre if despues else None,
            })

    return conflictos


ORDENES_RANKING = ('piezas', 'ingreso', 'ganancia')


def ordenar_ranking(filas, orden):
    """Ordena el ranking descendente por `orden`, dejando "sin clasificar"
    siempre al final. `orden` desconocido cae a piezas."""
    if orden not in ORDENES_RANKING:
        orden = 'piezas'
    filas = sorted(filas, key=lambda f: (-f[orden], -f['ingreso']))
    filas.sort(key=lambda f: f['tipo'] is None)
    return filas
