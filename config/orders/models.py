import uuid

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

    deposit  = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    is_paid  = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pedido'
        verbose_name_plural = 'Pedidos'
        ordering = ['-created_at']

    @property
    def total(self):
        return sum(item.subtotal for item in self.items.all())

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def ganancia(self):
        return self.total_items * 100

    @property
    def balance_due(self):
        return self.total - self.deposit

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
