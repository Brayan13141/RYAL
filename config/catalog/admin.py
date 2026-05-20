from django.contrib import admin
from .models import Category, Tag, Product, ProductImage, ProductVariant, VolumeTier


class VolumeTierInline(admin.TabularInline):
    model = VolumeTier
    extra = 1
    fields = ['min_qty', 'unit_price']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'shipping_cost', 'profit_margin', 'is_active', 'display_order']
    list_editable = ['shipping_cost', 'profit_margin', 'is_active', 'display_order']
    list_filter = ['is_active', 'parent']
    search_fields = ['name']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [VolumeTierInline]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'color_hex']


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ['image', 'is_cover', 'display_order']


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ['name', 'attributes', 'extra_price', 'stock', 'is_active']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        'sku', 'name', 'category', 'base_price', 'get_final_price',
        'min_order_qty', 'requires_shipping_display', 'status', 'is_active'
    ]
    list_filter = ['category', 'status', 'is_active', 'tags']
    search_fields = ['sku', 'name', 'description']
    list_editable = ['status', 'is_active']
    filter_horizontal = ['tags']
    readonly_fields = ['get_final_price', 'requires_shipping_display']
    inlines = [ProductImageInline, ProductVariantInline]
    fieldsets = (
        ('Información principal', {
            'fields': ('sku', 'name', 'description', 'category', 'tags', 'status', 'is_active')
        }),
        ('Precios', {
            'fields': ('base_price', 'shipping_override', 'get_final_price', 'requires_shipping_display', 'min_order_qty'),
            'description': 'Precio final = base + envío (categoría o override) + margen de ganancia'
        }),
        ('Proveedor', {
            'fields': ('supplier_url',),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description='Precio final')
    def get_final_price(self, obj):
        return f'${obj.final_price} MXN'

    @admin.display(description='Requiere envío', boolean=True)
    def requires_shipping_display(self, obj):
        return obj.requires_shipping
