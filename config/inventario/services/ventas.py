"""Venta escaneada (Modo Vender) y ligado de un par a una venta del bot."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q
from django.utils import timezone

from catalog.models import TipoArticulo
from inventario.models import MovimientoPar, Par, fmt_talla
from inventario.services.bot import parsear_venta_tenis
from inventario.services.escaneo import Resultado, _rojo
from negocio.models import Cliente, Pago, Pedido, PedidoItem
from negocio.services import MOSTRADOR_TELEFONO, cargar_aliases, resolver_tipo

_METODOS_VALIDOS = {m[0] for m in Pago.METODO_CHOICES}
_TIPOS_VENTA = {t[0] for t in Pedido.TIPO_VENTA_CHOICES}


def _descripcion(n_articulos, partes):
    """Texto del pedido: '2 art.: Jordan 4 negro 27 ×1, ...' (mismo formato que la venta del bot)."""
    return f'{n_articulos} art.: ' + ', '.join(partes)


def vender_pares(lineas, *, metodo_pago, usuario, tipo_venta=None):
    """Venta de mostrador con pares escaneados: Pedido PAGADO + un ítem por par + Pago.

    `lineas`: lista de (Par, precio). `tipo_venta`: mayoreo, menudeo o None (sin
    clasificar). Todo o nada: si algo falla, `ValueError` y no se crea ni se mueve nada.
    """
    if not lineas:
        raise ValueError('La venta no tiene pares.')
    if metodo_pago not in _METODOS_VALIDOS:
        raise ValueError(f'Método de pago inválido: {metodo_pago!r}')
    if tipo_venta is not None and tipo_venta not in _TIPOS_VENTA:
        raise ValueError(f'Tipo de venta inválido: {tipo_venta!r}')
    vistos = set()
    for par, precio in lineas:
        if par.pk in vistos:
            raise ValueError(f'El par {par.codigo} está repetido.')
        vistos.add(par.pk)
        if not isinstance(precio, Decimal) or not precio.is_finite() or precio < 0:
            raise ValueError(f'Precio inválido para {par.codigo}.')

    with transaction.atomic():
        # Candado en orden de pk: dos ventas cruzadas no se bloquean entre sí.
        bloqueados = {
            p.pk: p for p in Par.objects.select_for_update(of=('self',))
            .select_related('tipo', 'color', 'talla').filter(pk__in=vistos).order_by('pk')}
        for par, _ in lineas:
            actual = bloqueados.get(par.pk)
            if actual is None or actual.estado != Par.Estado.EN_STOCK:
                raise ValueError(f'El par {par.codigo} no está en stock.')

        cliente, _creado = Cliente.objects.get_or_create(
            telefono=MOSTRADOR_TELEFONO, defaults={'nombre': 'Mostrador'})
        pedido = Pedido.objects.create(
            cliente=cliente, descripcion='', costo_producto=Decimal('0'),
            precio_venta=Decimal('0'), envio=Decimal('0'),
            estado=Pedido.PAGADO, origen=Pedido.TIENDA, tipo_venta=tipo_venta)

        total_precio = Decimal('0')
        total_costo = Decimal('0')
        partes = []
        for par, precio in lineas:
            p = bloqueados[par.pk]
            nombre = f'{p.tipo.nombre} {p.color.nombre} {fmt_talla(p.talla.mx_cm)}'
            item = PedidoItem.objects.create(
                pedido=pedido, sku_snapshot=p.codigo, nombre_snapshot=nombre,
                cantidad=1, costo_unitario=p.costo, precio_unitario=precio)
            par.transicionar('vendido', usuario=usuario, origen='escaner', pedido_item=item)
            total_precio += precio
            total_costo += p.costo
            partes.append(f'{nombre} ×1')

        pedido.precio_venta = total_precio
        pedido.costo_producto = total_costo
        pedido.descripcion = _descripcion(len(lineas), partes)
        pedido.save(update_fields=['precio_venta', 'costo_producto', 'descripcion'])
        Pago.objects.create(
            pedido=pedido, fecha=timezone.localdate(),
            monto=total_precio, metodo_pago=metodo_pago)
    return pedido


def asignado_por_bot(par):
    """True si la última vez que el par pasó a vendido fue por el bot."""
    mov = (MovimientoPar.objects.filter(par=par, a_estado='vendido')
           .order_by('-creado_at', '-pk').first())
    return mov is not None and mov.origen == 'bot'


def ligar_a_item(par, item, *, usuario):
    """Liga un par escaneado a un ítem de una venta del bot (no toca costo ni pagos).

    Con lugar libre, solo lo liga. Con el ítem lleno, intercambia con el par
    asignado por el bot más reciente (que vuelve a stock) si es la misma variante.
    Una venta cancelada no recibe pares.
    """
    with transaction.atomic():
        # Orden de candados: Pedido -> ítem -> pares (igual que cancelar/quitar).
        # Lectura sin candado solo para saber qué pedido bloquear; todo se revalida abajo.
        pedido_id = (PedidoItem.objects.filter(pk=item.pk)
                     .values_list('pedido_id', flat=True).first())
        if pedido_id is None:
            return _rojo('Esa venta ya no existe', par)
        pedido = Pedido.objects.select_for_update().get(pk=pedido_id)
        # Se relee bajo candado: la cantidad que vale es la de ahora, no la del objeto recibido.
        item = PedidoItem.objects.select_for_update().filter(pk=item.pk).first()
        if item is None:
            return _rojo('Esa venta ya no existe', par)
        # El par escaneado y los ligados, en orden de pk, en una sola consulta.
        bloqueados = list(
            Par.objects.select_for_update(of=('self',)).select_related('tipo', 'color', 'talla')
            .filter(Q(pk=par.pk) | Q(pedido_item=item)).order_by('pk'))
        par = next(p for p in bloqueados if p.pk == par.pk)
        ligados = sorted((p for p in bloqueados if p.pedido_item_id == item.pk),
                         key=lambda p: (p.vendido_at is None, p.vendido_at, p.pk), reverse=True)

        if pedido.estado == Pedido.CANCELADO:
            return _rojo('Esa venta está cancelada', par)
        if par.estado != Par.Estado.EN_STOCK:
            return _rojo(
                f'No se puede vender: el par está en estado {par.get_estado_display()}', par)

        if item.cantidad > len(ligados):
            par.transicionar('vendido', usuario=usuario, origen='escaner', pedido_item=item)
            return Resultado('verde', 'Par ligado a la venta', par, None)

        candidato = next((p for p in ligados if asignado_por_bot(p)), None)
        if candidato is None:
            return _rojo('Esa venta ya tiene todos sus pares', par)
        if (candidato.tipo_id, candidato.color_id, candidato.talla_id) != (
                par.tipo_id, par.color_id, par.talla_id):
            return _rojo('El par no es de la misma variante que el de la venta', par)

        candidato.transicionar(
            'en_stock', usuario=usuario, origen='deshacer',
            nota=f'Intercambio por escaneo de {par.codigo}')
        par.transicionar('vendido', usuario=usuario, origen='escaner', pedido_item=item)
        return Resultado('verde', f'Intercambiado por {candidato.codigo}', par, None)


_MAX_ESCANEO_BOT = 200  # últimos ítems del bot que se revisan
_MAX_ITEMS_BOT = 20     # candidatos que se devuelven


def items_bot_pendientes(par):
    """Ítems de ventas del bot (no canceladas) de la misma variante, color y talla del par.

    Devuelve `[(item, libres)]`, más recientes primero. Incluye los llenos con pares
    del bot (`libres == 0`): con ellos se puede intercambiar. El tipo, el color y la
    talla salen del texto tecleado con la misma regla que usa el bot al vender.
    """
    # Un ítem lleno solo sirve si alguno de sus pares lo asignó el bot (intercambio): los
    # llenos por escaneo son opciones muertas y no deben gastar la ventana de escaneo.
    con_par_del_bot = Exists(MovimientoPar.objects.filter(
        par__pedido_item=OuterRef('pk'), a_estado='vendido', origen='bot'))
    candidatos = list(
        PedidoItem.objects
        .filter(sku_snapshot='TIENDA-BOT', pedido__origen=Pedido.TIENDA)
        .exclude(pedido__estado=Pedido.CANCELADO)
        .select_related('pedido').annotate(n_pares=Count('pares'))
        .filter(Q(n_pares__lt=F('cantidad')) | con_par_del_bot)
        .order_by('-pedido__fecha', '-pedido__created_at', '-pk')[:_MAX_ESCANEO_BOT])
    if not candidatos:
        return []
    tipos = list(TipoArticulo.objects.all())  # una vez por llamada, no por ítem
    aliases = cargar_aliases()
    salida = []
    for item in candidatos:
        tipo = resolver_tipo(item.nombre_snapshot, tipos, aliases)
        if tipo is None or tipo.pk != par.tipo_id:
            continue
        color, talla, _faltas = parsear_venta_tenis(item.nombre_snapshot, tipo)
        if color is None or talla is None:
            continue
        if (color.pk, talla.pk) != (par.color_id, par.talla_id):
            continue
        libres = max(0, item.cantidad - item.n_pares)
        if libres == 0 and not any(
                asignado_por_bot(p) for p in item.pares.all()):
            continue  # lleno y sin par del bot: ligar_a_item lo rechazaría
        salida.append((item, libres))
        if len(salida) == _MAX_ITEMS_BOT:
            break
    return salida


def _nota_obligatoria(nota):
    nota = (nota or '').strip()
    if not nota:
        raise ValueError('La nota es obligatoria.')
    return nota


def _reembolsar(pedido, monto, nota):
    """Pago negativo: conserva el cobro original y deja la caja cuadrada."""
    if monto == 0:
        return
    original = max(pedido.pagos.all(), key=lambda p: p.monto, default=None)
    Pago.objects.create(
        pedido=pedido, fecha=timezone.localdate(), monto=-monto,
        metodo_pago=original.metodo_pago if original else Pago.EFECTIVO,
        notas=f'Reembolso: {nota}'[:200])


def _cancelar_bloqueado(pedido, usuario, nota):
    """Cancela un pedido ya bloqueado: pares a stock, estado y reembolso del saldo pagado."""
    # Orden de candados: Pedido (ya tomado) -> ítems -> pares.
    list(PedidoItem.objects.select_for_update().filter(pedido=pedido).order_by('pk'))
    pares = Par.objects.select_for_update().filter(pedido_item__pedido=pedido).order_by('pk')
    for par in pares:
        par.transicionar('en_stock', usuario=usuario, origen='deshacer', nota=nota)
    pagado = sum((p.monto for p in pedido.pagos.all()), Decimal('0'))
    _reembolsar(pedido, pagado, nota)
    pedido.estado = Pedido.CANCELADO
    pedido.save(update_fields=['estado'])


def cancelar_pedido(pedido, *, usuario, nota):
    """Deshace una venta completa: pares a stock y reembolso (Pago negativo) de lo cobrado."""
    nota = _nota_obligatoria(nota)
    with transaction.atomic():
        pedido = Pedido.objects.select_for_update().get(pk=pedido.pk)
        if pedido.estado == Pedido.CANCELADO:
            raise ValueError('El pedido ya está cancelado')
        _cancelar_bloqueado(pedido, usuario, nota)


def quitar_par_de_pedido(par, *, usuario, nota):
    """Quita un par de una venta: baja el ítem, recalcula el pedido y reembolsa su precio."""
    nota = _nota_obligatoria(nota)
    with transaction.atomic():
        # Lectura sin candado solo para saber qué pedido bloquear; todo se revalida abajo.
        previo = Par.objects.get(pk=par.pk)
        if previo.pedido_item_id is None:
            raise ValueError(f'El par {previo.codigo} no está en una venta.')
        pedido_id = PedidoItem.objects.values_list('pedido_id', flat=True).get(
            pk=previo.pedido_item_id)
        # Orden de candados: Pedido -> ítem -> par (igual que cancelar_pedido).
        pedido = Pedido.objects.select_for_update().get(pk=pedido_id)
        item_id = previo.pedido_item_id
        item = PedidoItem.objects.select_for_update().filter(pk=item_id).first()
        par = Par.objects.select_for_update().get(pk=par.pk)
        if (item is None or par.estado != Par.Estado.VENDIDO
                or par.pedido_item_id != item.pk):
            raise ValueError(f'El par {par.codigo} no está en una venta.')
        if pedido.estado != Pedido.PAGADO:
            raise ValueError('Solo se puede quitar un par de un pedido pagado.')

        par.transicionar('en_stock', usuario=usuario, origen='deshacer', nota=nota)
        precio = item.precio_unitario
        if item.cantidad > 1:
            item.cantidad -= 1
            item.save(update_fields=['cantidad'])
        else:
            item.delete()

        items = list(pedido.items.all())
        if not items:
            _cancelar_bloqueado(pedido, usuario, nota)
            return
        pedido.precio_venta = sum((i.cantidad * i.precio_unitario for i in items), Decimal('0'))
        pedido.costo_producto = sum((i.cantidad * i.costo_unitario for i in items), Decimal('0'))
        pedido.descripcion = _descripcion(
            len(items), [f'{i.nombre_snapshot} ×{i.cantidad}' for i in items])
        pedido.save(update_fields=['precio_venta', 'costo_producto', 'descripcion'])
        _reembolsar(pedido, precio, nota)
