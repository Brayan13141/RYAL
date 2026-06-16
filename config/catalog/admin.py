from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from .models import Category, Tag, Product, ProductImage, ProductVariant, VolumeTier, PendingProduct


class VolumeTierInline(admin.TabularInline):
    model = VolumeTier
    extra = 1
    fields = ['min_qty', 'unit_price']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'shipping_cost', 'profit_margin', 'min_order_qty', 'min_qty_per_item', 'is_active', 'display_order']
    list_editable = ['shipping_cost', 'profit_margin', 'min_order_qty', 'min_qty_per_item', 'is_active', 'display_order']
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


@admin.register(PendingProduct)
class PendingProductAdmin(admin.ModelAdmin):
    list_display       = ['thumbnail_img', 'modaverse_name', 'display_name', 'category', 'base_price', 'status', 'link_proveedor', 'created_at']
    list_display_links = ['modaverse_name']
    list_editable      = ['display_name', 'base_price', 'category']
    list_filter        = ['status', 'category__parent']
    search_fields      = ['display_name', 'modaverse_name', 'supplier_url']
    readonly_fields    = ['thumbnail_img', 'modaverse_name', 'supplier_url', 'raw_data', 'created_at', 'reviewed_at']
    actions            = ['approve_selected', 'reject_selected']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.GET.get('status__exact'):
            return qs.filter(status='pending')
        return qs

    @admin.display(description='Foto')
    def thumbnail_img(self, obj):
        url = obj.cover_image.url if obj.cover_image else (obj.raw_data or {}).get('image_url', '')
        if url:
            return format_html(
                '<img src="{}" style="height:60px;width:60px;object-fit:cover;border-radius:4px;" loading="lazy">',
                url,
            )
        return format_html('<span style="color:#888;font-size:11px;">—</span>')

    @admin.display(description='Proveedor')
    def link_proveedor(self, obj):
        if obj.supplier_url and obj.supplier_url.startswith('http'):
            return format_html('<a href="{}" target="_blank">Ver ↗</a>', obj.supplier_url)
        return '—'

    @admin.action(description='✓ Aprobar seleccionados → agregar al catálogo')
    def approve_selected(self, request, queryset):
        count, errors = 0, []
        for pending in queryset.filter(status='pending'):
            try:
                pending.approve()
                count += 1
            except Exception as e:
                errors.append(f'{pending.display_name}: {e}')
        msg = f'{count} producto(s) aprobado(s) y agregado(s) al catálogo.'
        if errors:
            msg += ' Errores: ' + '; '.join(errors)
        self.message_user(request, msg)

    @admin.action(description='✗ Rechazar seleccionados')
    def reject_selected(self, request, queryset):
        count = 0
        for pending in queryset.filter(status='pending'):
            pending.reject()
            count += 1
        self.message_user(request, f'{count} producto(s) rechazado(s).')
