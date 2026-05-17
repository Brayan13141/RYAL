from django.contrib import admin
from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ['sku_snapshot', 'name_snapshot', 'variant_snapshot', 'price_snapshot', 'subtotal']
    fields = ['sku_snapshot', 'name_snapshot', 'variant_snapshot', 'quantity', 'price_snapshot', 'subtotal']
    can_delete = False

    @admin.display(description='Subtotal')
    def subtotal(self, obj):
        return f'${obj.subtotal} MXN'


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'order_code', 'customer_name', 'customer_phone', 'get_total',
        'total_items', 'status', 'created_at'
    ]
    list_filter = ['status', 'created_at']
    search_fields = ['order_code', 'customer_name', 'customer_phone', 'customer_email']
    list_editable = ['status']
    readonly_fields = ['order_code', 'created_at', 'updated_at', 'get_total']
    inlines = [OrderItemInline]
    date_hierarchy = 'created_at'
    fieldsets = (
        ('Pedido', {
            'fields': ('order_code', 'status', 'notes', 'get_total')
        }),
        ('Cliente', {
            'fields': ('user', 'customer_name', 'customer_phone', 'customer_email')
        }),
        ('Fechas', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description='Total')
    def get_total(self, obj):
        return f'${obj.total} MXN'
