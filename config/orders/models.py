import uuid
from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from catalog.models import Product, ProductVariant


SUPPLIER_ORDER_STATUS = [
    ('pending',  'Pendiente'),
    ('running',  'En progreso'),
    ('done',     'Completado'),
    ('partial',  'Parcial'),
    ('failed',   'Fallido'),
]

SUPPLIER_ITEM_STATUS = [
    ('pending',           'Pendiente'),
    ('added',             'Agregado'),
    ('variant_not_found', 'Variante no encontrada'),
    ('no_url',            'Sin URL de proveedor'),
]


class SavedCartItem(models.Model):
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_cart')
    cart_key  = models.CharField(max_length=60)
    product   = models.ForeignKey(Product, on_delete=models.CASCADE)
    variant   = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True)
    variant_name = models.CharField(max_length=100, blank=True)
    quantity  = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = [('user', 'cart_key')]
        verbose_name = 'Ítem de carrito guardado'


class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('confirmed', 'Confirmado'),
        ('in_preparation', 'En preparación'),
        ('shipped', 'Enviado'),
        ('delivered', 'Entregado'),
        ('cancelled', 'Cancelado'),
    ]

    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders'
    )
    order_code     = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    tracking_token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=20)
    customer_email = models.EmailField(blank=True)

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)

    deposit             = models.DecimalField(max_digits=8, decimal_places=2, default=0)  # deprecado: reemplazado por OrderPayment
    descuento_aplicado  = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    is_paid  = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    seen_at = models.DateTimeField(
        null=True, blank=True, default=None,
        help_text='Cuándo el staff vio este pedido en el panel (marca global, no por usuario).',
    )

    class Meta:
        verbose_name = 'Pedido'
        verbose_name_plural = 'Pedidos'
        ordering = ['-created_at']

    @property
    def total(self):
        return sum(item.subtotal for item in self.items.all()) - self.descuento_aplicado

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def costo_mercancia(self):
        """Lo que hay que pagarle al proveedor por este pedido.

        Misma cadena de respaldo que `ganancia` — snapshot, costo vivo del
        producto, y $100 por unidad como último recurso — a propósito: si las
        dos divergieran, la caja y el reporte contarían costos distintos para
        el mismo pedido. Hay un test que fija la consistencia.
        """
        from decimal import Decimal
        total = Decimal('0')
        for item in self.items.all():
            if item.cost_snapshot is not None:
                total += item.cost_snapshot * item.quantity
            elif item.product_id:
                try:
                    cost = item.product.effective_base_price + item.product.effective_shipping
                    total += cost * item.quantity
                except Exception:
                    total += (item.price_snapshot - Decimal('100')) * item.quantity
            else:
                # OJO: el último recurso de `ganancia` asume $100 de GANANCIA
                # por unidad, no un costo de $100. El costo implícito es lo que
                # queda del precio. Leerlo al revés descuadra las dos.
                total += (item.price_snapshot - Decimal('100')) * item.quantity
        return total

    @property
    def ganancia(self):
        from decimal import Decimal
        total = Decimal('0')
        for item in self.items.all():
            if item.cost_snapshot is not None:
                total += (item.price_snapshot - item.cost_snapshot) * item.quantity
            elif item.product_id:
                try:
                    cost = item.product.effective_base_price + item.product.effective_shipping
                    total += (item.price_snapshot - cost) * item.quantity
                except Exception:
                    total += Decimal('100') * item.quantity
            else:
                total += Decimal('100') * item.quantity
        return total - self.descuento_aplicado

    @property
    def total_pagado(self):
        return sum((p.monto for p in self.payments.all()), Decimal('0'))

    @property
    def balance_due(self):
        return self.total - self.total_pagado

    def recalc_paid(self):
        """Sincroniza is_paid con el saldo real (una sola fuente de verdad)."""
        paid = self.balance_due <= 0
        if self.is_paid != paid:
            self.is_paid = paid
            self.save(update_fields=['is_paid', 'updated_at'])

    def __str__(self):
        return f'{self.order_code or f"#{self.pk}"} — {self.customer_name} ({self.get_status_display()})'


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, related_name='order_items'
    )
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.SET_NULL, null=True, blank=True
    )
    quantity = models.PositiveIntegerField(default=1)

    # Snapshots — prices/names may change; order history stays accurate
    price_snapshot = models.DecimalField(max_digits=8, decimal_places=2)
    cost_snapshot  = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                         help_text='Costo (base + envío) al momento del pedido.')
    sku_snapshot = models.CharField(max_length=100)
    name_snapshot = models.CharField(max_length=200)
    variant_snapshot = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = 'Ítem de pedido'
        verbose_name_plural = 'Ítems de pedido'

    @property
    def subtotal(self):
        return self.price_snapshot * self.quantity

    def __str__(self):
        return f'{self.quantity}x {self.sku_snapshot} — Pedido #{self.order_id}'


class SupplierOrder(models.Model):
    order           = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='supplier_order')
    status          = models.CharField(max_length=20, choices=SUPPLIER_ORDER_STATUS, default='pending')
    cart_script     = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pedido proveedor'

    def __str__(self):
        return f'SupplierOrder #{self.order.order_code} ({self.get_status_display()})'


class SupplierOrderItem(models.Model):
    supplier_order = models.ForeignKey(SupplierOrder, on_delete=models.CASCADE, related_name='items')
    order_item     = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='supplier_item')
    supplier_url   = models.URLField(max_length=500, blank=True)
    variant_target = models.CharField(max_length=200, blank=True)
    status         = models.CharField(max_length=30, choices=SUPPLIER_ITEM_STATUS, default='pending')
    notes          = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Ítem de pedido proveedor'

    def __str__(self):
        return f'{self.order_item.sku_snapshot} → {self.get_status_display()}'


class OrderPayment(models.Model):
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

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=8, decimal_places=2)
    metodo_pago = models.CharField(max_length=20, choices=METODO_CHOICES, default=EFECTIVO)
    notas = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha', 'id']
        verbose_name = 'Pago de pedido'
        verbose_name_plural = 'Pagos de pedido'

    def __str__(self):
        return f'Pago ${self.monto} — Pedido #{self.order_id}'
