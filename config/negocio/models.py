import datetime
from decimal import Decimal
from django.db import models
from django.conf import settings


class Cliente(models.Model):
    nombre = models.CharField(max_length=200)
    telefono = models.CharField(max_length=20, unique=True)
    descuento = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return f"{self.nombre} ({self.telefono})"


class Pedido(models.Model):
    PENDIENTE = 'pendiente'
    PAGADO = 'pagado'
    CANCELADO = 'cancelado'
    ESTADO_CHOICES = [
        (PENDIENTE, 'Pendiente'),
        (PAGADO, 'Pagado'),
        (CANCELADO, 'Cancelado'),
    ]

    WHATSAPP = 'whatsapp'
    TIENDA = 'tienda'
    BOT = 'bot'
    ORIGEN_CHOICES = [
        (WHATSAPP, 'WhatsApp'),
        (TIENDA, 'Tienda física'),
        (BOT, 'Bot'),
    ]

    cliente = models.ForeignKey(
        Cliente, on_delete=models.PROTECT, related_name='pedidos',
        null=True, blank=True,
    )
    fecha = models.DateField(default=datetime.date.today)
    descripcion = models.TextField(blank=True)
    costo_producto = models.DecimalField(max_digits=10, decimal_places=2)
    precio_venta = models.DecimalField(max_digits=10, decimal_places=2)
    envio = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    descuento_aplicado = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0'),
    )
    codigo_descuento = models.ForeignKey(
        'catalog.CodigoDescuento', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pedidos_aplicados',
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=PENDIENTE)
    origen = models.CharField(max_length=20, choices=ORIGEN_CHOICES, default=WHATSAPP)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-created_at']

    def __str__(self):
        nombre = self.cliente.nombre if self.cliente else 'Mostrador'
        return f"Pedido #{self.pk} — {nombre}"

    @property
    def total_a_cobrar(self):
        return self.precio_venta + self.envio - self.descuento_aplicado

    @property
    def ganancia(self):
        return self.precio_venta - self.costo_producto - self.descuento_aplicado

    @property
    def balance_pendiente(self):
        pagado = sum(p.monto for p in self.pagos.all())
        return self.total_a_cobrar - pagado


class Pago(models.Model):
    EFECTIVO = 'efectivo'
    TRANSFERENCIA = 'transferencia'
    TARJETA = 'tarjeta'
    OTRO = 'otro'
    METODO_CHOICES = [
        (EFECTIVO, 'Efectivo'),
        (TRANSFERENCIA, 'Transferencia'),
        (TARJETA, 'Tarjeta'),
        (OTRO, 'Otro'),
    ]

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='pagos')
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo_pago = models.CharField(max_length=20, choices=METODO_CHOICES, default=EFECTIVO)
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['fecha']

    def __str__(self):
        return f"Pago ${self.monto} — Pedido #{self.pedido_id}"


class Gasto(models.Model):
    COMPRA_PROVEEDOR = 'compra_proveedor'
    ENVIO = 'envio'
    OTRO = 'otro'
    CATEGORIA_CHOICES = [
        (COMPRA_PROVEEDOR, 'Compra al proveedor'),
        (ENVIO, 'Envío'),
        (OTRO, 'Otro'),
    ]

    fecha = models.DateField()
    descripcion = models.CharField(max_length=300)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    categoria = models.CharField(max_length=30, choices=CATEGORIA_CHOICES, default=OTRO)

    class Meta:
        ordering = ['-fecha']

    def __str__(self):
        return f"{self.descripcion} — ${self.monto}"


class PedidoItem(models.Model):
    """Línea de una venta. Snapshots de SKU/nombre/costo para preservar el
    historial aunque el producto del catálogo cambie o se borre."""
    pedido = models.ForeignKey('Pedido', on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(
        'catalog.Product', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ventas_items',  # permite agregar "más vendidos" por producto
    )
    sku_snapshot = models.CharField(max_length=100)
    nombre_snapshot = models.CharField(max_length=200)
    cantidad = models.PositiveIntegerField(default=1)
    costo_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f"{self.nombre_snapshot} ×{self.cantidad}"

    @property
    def subtotal(self):
        return self.precio_unitario * self.cantidad

    @property
    def costo_total(self):
        return self.costo_unitario * self.cantidad


class AjusteCaja(models.Model):
    """Ajuste manual del saldo de caja (arqueo). El saldo real de caja =
    cobrado − gastos + Σ(ajustes). El primer ajuste suele ser el 'Saldo inicial'
    (el efectivo que había antes de empezar a registrar ventas). Cada ajuste
    queda en el historial con su motivo y quién lo hizo (auditoría)."""
    fecha = models.DateField(default=datetime.date.today)
    monto = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Con signo: + suma a la caja, − resta.',
    )
    saldo_resultante = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Saldo de caja que quedó tras aplicar este ajuste.',
    )
    motivo = models.CharField(max_length=200)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ajustes_caja',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Ajuste de caja'
        verbose_name_plural = 'Ajustes de caja'

    def __str__(self):
        signo = '+' if self.monto >= 0 else ''
        return f'Ajuste {signo}{self.monto} — {self.motivo}'
