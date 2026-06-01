import datetime
from decimal import Decimal
from django.db import models


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

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='pedidos')
    fecha = models.DateField(default=datetime.date.today)
    descripcion = models.TextField()
    costo_producto = models.DecimalField(max_digits=10, decimal_places=2)
    precio_venta = models.DecimalField(max_digits=10, decimal_places=2)
    envio = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=PENDIENTE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-created_at']

    def __str__(self):
        return f"Pedido #{self.pk} — {self.cliente.nombre}"

    @property
    def total_a_cobrar(self):
        return self.precio_venta + self.envio

    @property
    def ganancia(self):
        return self.precio_venta - self.costo_producto

    @property
    def balance_pendiente(self):
        pagado = sum(p.monto for p in self.pagos.all())
        return self.total_a_cobrar - pagado


class Pago(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='pagos')
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=10, decimal_places=2)
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
