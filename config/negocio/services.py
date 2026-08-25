import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F

from catalog.models import Product, TipoArticulo
from .models import Pedido, PedidoItem, Pago, Cliente


class VentaInvalida(Exception):
    """Payload de venta inválido (SKU inexistente/inactivo, cantidad o precio inválidos)."""


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


@transaction.atomic
def crear_pedido_tienda_bot(*, items, envio=Decimal('0')):
    """Crea un pedido de tienda física via bot. Sin SKU, sin normalize_telefono.

    `items`: lista de {description, price, qty}
    """
    if not items:
        raise VentaInvalida('La venta no tiene ítems.')

    cliente, _ = Cliente.objects.get_or_create(
        telefono=MOSTRADOR_TELEFONO,
        defaults={'nombre': 'Mostrador'},
    )

    precio_total = sum(Decimal(str(i['price'])) * int(i['qty']) for i in items)
    partes_desc = []
    pedido = Pedido.objects.create(
        cliente=cliente,
        descripcion='',
        costo_producto=Decimal('0'),
        precio_venta=precio_total,
        envio=Decimal(str(envio)),
        estado=Pedido.PAGADO,
        origen=Pedido.TIENDA,
    )

    tipos = list(TipoArticulo.objects.all())
    total_costo = Decimal('0')

    for item in items:
        nombre_snap = (str(item.get('description') or '').strip() or 'ítem tienda')[:200]
        qty = int(item['qty'])
        precio_u = Decimal(str(item['price']))
        costo_u = next((t.costo for t in tipos if t.matches(nombre_snap)), Decimal('0'))
        PedidoItem.objects.create(
            pedido=pedido,
            product=None,
            sku_snapshot='TIENDA-BOT',
            nombre_snapshot=nombre_snap,
            cantidad=qty,
            costo_unitario=costo_u,
            precio_unitario=precio_u,
        )
        total_costo += costo_u * qty
        partes_desc.append(f'{nombre_snap[:30]} ×{qty}')

    pedido.costo_producto = total_costo
    pedido.descripcion = f'{len(items)} art.: ' + ', '.join(partes_desc)
    pedido.save(update_fields=['costo_producto', 'descripcion'])

    Pago.objects.create(
        pedido=pedido,
        fecha=datetime.date.today(),
        monto=precio_total,
        metodo_pago=Pago.EFECTIVO,
    )
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
    ingreso, costo, ganancia y sin_desglose. Sin clasificar va siempre al
    final: esconderlo descuadraría los totales sin que se note.
    """
    from catalog.services import buscar_tipo_articulo

    pedidos = Pedido.objects.filter(estado=Pedido.PAGADO)
    if fecha_ini is not None:
        pedidos = pedidos.filter(fecha__gte=fecha_ini)
    if fecha_fin is not None:
        pedidos = pedidos.filter(fecha__lt=fecha_fin)

    tipos = list(TipoArticulo.objects.all())
    acumulado = {}

    def fila_de(tipo):
        nombre = tipo.nombre if tipo else None
        if nombre not in acumulado:
            acumulado[nombre] = {
                'tipo': nombre, 'piezas': 0, 'ingreso': Decimal('0'),
                'costo': Decimal('0'), 'sin_desglose': 0,
            }
        return acumulado[nombre]

    for pedido in pedidos.prefetch_related('items'):
        items = list(pedido.items.all())

        if not items:
            # Pedido capturado a mano: no hay campo de cantidad, la cantidad
            # vive dentro del texto. Cuenta 1 pieza y se declara en pantalla;
            # parsear prosa para afinar eso cambia un error chico por un
            # riesgo permanente.
            fila = fila_de(buscar_tipo_articulo(pedido.descripcion, tipos=tipos))
            fila['piezas'] += 1
            fila['ingreso'] += pedido.precio_venta - pedido.descuento_aplicado
            fila['costo'] += pedido.costo_producto
            fila['sin_desglose'] += 1
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

            fila = fila_de(buscar_tipo_articulo(item.nombre_snapshot, tipos=tipos))
            fila['piezas'] += item.cantidad
            fila['ingreso'] += subtotal - parte
            fila['costo'] += item.costo_unitario * item.cantidad

    filas = []
    for fila in acumulado.values():
        fila['ganancia'] = fila['ingreso'] - fila['costo']
        filas.append(fila)

    return ordenar_ranking(filas, 'piezas')


ORDENES_RANKING = ('piezas', 'ingreso', 'ganancia')


def ordenar_ranking(filas, orden):
    """Ordena el ranking descendente por `orden`, dejando "sin clasificar"
    siempre al final. `orden` desconocido cae a piezas."""
    if orden not in ORDENES_RANKING:
        orden = 'piezas'
    filas = sorted(filas, key=lambda f: (-f[orden], -f['ingreso']))
    filas.sort(key=lambda f: f['tipo'] is None)
    return filas
