from decimal import Decimal

from django.db import models
from django.utils.text import slugify


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank=True)
    parent = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='subcategories'
    )
    image = models.ImageField(upload_to='categories/', blank=True)
    # Pricing rules — modify in admin to add new categories without code changes
    shipping_cost   = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    profit_margin   = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    min_order_qty   = models.PositiveIntegerField(default=1, help_text='Mínimo de piezas totales de esta categoría por pedido')
    min_qty_per_item = models.PositiveIntegerField(default=0, help_text='Mínimo de piezas por modelo en el carrito (0 = sin restricción por modelo, ej: calzado=12)')
    is_active       = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    banner_text   = models.TextField(blank=True)
    size_group    = models.ForeignKey(
        'SizeGroup', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='categories',
    )
    has_color_variants = models.BooleanField(
        default=False,
        help_text='Las imágenes del producto representan variantes de color distintas (ej. calzado por colorway)',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Categoría'
        verbose_name_plural = 'Categorías'
        ordering = ['display_order', 'name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    color_hex = models.CharField(max_length=7, default='#C9A84C')

    class Meta:
        verbose_name = 'Tag'
        verbose_name_plural = 'Tags'

    def __str__(self):
        return self.name


class Product(models.Model):
    STATUS_CHOICES = [
        ('available', 'Disponible'),
        ('sold_out', 'Agotado'),
        ('coming_soon', 'Próximamente'),
    ]

    sku = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name='products'
    )
    base_price = models.DecimalField(max_digits=8, decimal_places=2)

    # Minimum units per order (e.g. some suppliers require 3+)
    min_order_qty = models.PositiveIntegerField(default=1)

    # Per-product overrides — take precedence over subcategory settings
    has_color_variants = models.BooleanField(
        default=False,
        help_text='Las imágenes representan colores/variantes distintas (sobreescribe el de la subcategoría)',
    )
    size_group = models.ForeignKey(
        'SizeGroup', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='products',
        help_text='Grupo de tallas personalizado (sobreescribe el de la subcategoría)',
    )

    # Override category shipping for specific products (null = use category default)
    shipping_override = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text='Dejar vacío para usar el costo de envío de la categoría'
    )
    # Override final price entirely (null = use formula base+envío+margen)
    price_override = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text='Precio final fijo. Cuando se especifica ignora base_price, envío y margen.'
    )

    # Colores seleccionables (variantes Modaverse) — dimensión independiente de la talla.
    # Lista de strings, ej. ["Rojo burdeos", "Negro"]. Vacía = el producto no pide color.
    variant_colors = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    tags = models.ManyToManyField(Tag, blank=True)
    supplier_url = models.URLField(blank=True, help_text='URL del producto en el proveedor')
    modaverse_name = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Nombre crudo de Modaverse (productName sin _clean_name). '
                  'Se usa para encontrar el producto en el carrito de Modaverse.',
    )
    is_active   = models.BooleanField(default=True)
    auto_deactivated = models.BooleanField(
        default=False,
        help_text='Desactivado por reconcile_catalog (removido del proveedor). '
                  'Distingue de ocultamientos manuales; habilita reactivación segura.',
    )
    is_featured = models.BooleanField(default=False, help_text='Aparece en "Nuevos ingresos" del inicio')
    display_order = models.PositiveIntegerField(default=0)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Producto'
        verbose_name_plural = 'Productos'
        ordering = ['display_order', '-created_at']

    @property
    def _root_category(self):
        """Categoría raíz: la jerarquía es de 2 niveles (padre → subcategoría).
        Margen y envío viven en la raíz; las subcategorías no los definen."""
        cat = self.category
        return cat.parent if cat.parent_id else cat

    @property
    def effective_shipping(self):
        if self.shipping_override is not None:
            return self.shipping_override
        return self._root_category.shipping_cost

    @property
    def final_price(self):
        if self.price_override is not None:
            return self.price_override
        bp = self.base_price if isinstance(self.base_price, Decimal) else Decimal(str(self.base_price))
        # Margen desde la raíz (no la subcategoría directa); envío ya cascadea en effective_shipping
        return bp + self.effective_shipping + self._root_category.profit_margin

    @property
    def effective_size_group(self):
        """Cascade: product > subcategoría > categoría padre."""
        cat  = self.category
        root = cat.parent if cat.parent_id else cat
        return self.size_group or cat.size_group or root.size_group

    @property
    def effective_has_colorway(self):
        """True si el colorway está activo en cualquier nivel de la jerarquía."""
        cat  = self.category
        root = cat.parent if cat.parent_id else cat
        return (
            self.has_color_variants
            or cat.has_color_variants
            or root.has_color_variants
        )

    @property
    def effective_min_qty(self):
        cat = self.category
        root = cat.parent if cat.parent_id else cat
        return max(self.min_order_qty, root.min_qty_per_item)

    @property
    def requires_shipping(self):
        return self.effective_shipping > 0

    @property
    def cover_image(self):
        img = self.images.filter(is_cover=True).first()
        if img is None:
            img = self.images.first()
        return img

    def __str__(self):
        return f'{self.sku} — {self.name}'


class PendingProduct(models.Model):
    """Productos nuevos detectados en el scrape, en espera de aprobación manual."""
    STATUS_CHOICES = [
        ('pending',  'Pendiente'),
        ('approved', 'Aprobado'),
        ('rejected', 'Rechazado'),
    ]

    supplier_url   = models.CharField(max_length=600, unique=True)
    display_name   = models.CharField(max_length=500)
    modaverse_name = models.CharField(max_length=500, blank=True)
    category       = models.ForeignKey(
        'Category', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pending_products',
    )
    base_price   = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    raw_data     = models.JSONField(default=dict)
    cover_image  = models.ImageField(upload_to='pending/', blank=True)
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    notes        = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    reviewed_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Producto pendiente'
        verbose_name_plural = 'Productos pendientes'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.display_name} [{self.get_status_display()}]'

    def approve(self):
        """Crea el Product en catálogo y marca como aprobado."""
        from django.utils import timezone

        sku            = self.raw_data.get('sku', '')
        size_group_pk  = self.raw_data.get('size_group_pk')
        variant_colors = self.raw_data.get('variant_colors', [])
        description    = self.raw_data.get('description', '')

        size_group = None
        if size_group_pk:
            try:
                size_group = SizeGroup.objects.get(pk=size_group_pk)
            except SizeGroup.DoesNotExist:
                pass

        product, created = Product.objects.get_or_create(
            sku=sku,
            defaults={
                'name':           self.display_name,
                'modaverse_name': self.modaverse_name or self.display_name,
                'category':       self.category,
                'base_price':     self.base_price,
                'supplier_url':   self.supplier_url,
                'variant_colors': variant_colors,
                'size_group':     size_group,
                'description':    description,
                'status':         'available',
                'is_active':      True,
            },
        )
        if not created:
            product.name           = self.display_name
            product.modaverse_name = self.modaverse_name or self.display_name
            product.base_price     = self.base_price
            product.save(update_fields=['name', 'modaverse_name', 'base_price'])

        self.status      = 'approved'
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'reviewed_at'])
        return product

    def reject(self, notes=''):
        from django.utils import timezone
        self.status      = 'rejected'
        self.notes       = notes
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'notes', 'reviewed_at'])


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images'
    )
    image = models.ImageField(upload_to='products/')
    is_cover = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    color_label = models.CharField(
        max_length=60, blank=True,
        help_text='Nombre visible del color en el carrito (ej. "Blanco", "Negro/Rojo"). Solo relevante en modo colorway.',
    )

    class Meta:
        verbose_name = 'Imagen de producto'
        verbose_name_plural = 'Imágenes de producto'
        ordering = ['-is_cover', 'display_order']

    def save(self, *args, **kwargs):
        if self.is_cover:
            ProductImage.objects.filter(
                product=self.product, is_cover=True
            ).exclude(pk=self.pk).update(is_cover=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Imagen de {self.product.sku} ({"portada" if self.is_cover else f"#{self.display_order}"})'


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='variants'
    )
    name = models.CharField(max_length=100)  # "Talla 42 / Blanco"
    # {"color": "negro"} | {"talla": "42", "color": "blanco"} | {"tono": "nude"}
    attributes = models.JSONField(default=dict)
    extra_price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Variante'
        verbose_name_plural = 'Variantes'

    @property
    def final_price(self):
        return self.product.final_price + self.extra_price

    def __str__(self):
        return f'{self.product.sku} — {self.name}'


class SiteConfig(models.Model):
    whatsapp = models.CharField(
        max_length=30, default='521XXXXXXXXXX',
        help_text='Número WhatsApp sin + ni espacios (ej: 5214771234567)',
    )
    track_message = models.TextField(
        blank=True, default='',
        help_text='Mensaje visible para el cliente en la página de seguimiento de pedido.',
    )

    # Hero copy
    hero_eyebrow = models.CharField(
        max_length=120, blank=True,
        default='Mayoreo · Importación directa · MX',
        help_text='Texto pequeño sobre el título del hero',
    )
    hero_title_em = models.CharField(
        max_length=120, blank=True,
        default='Tu inventario,',
        help_text='Primera línea del título (cursiva dorada)',
    )
    hero_title_strong = models.CharField(
        max_length=120, blank=True,
        default='siempre asegurado',
        help_text='Segunda línea del título (mayúsculas, grande)',
    )
    hero_sub = models.TextField(
        blank=True,
        default='Sneakers y gorras directo de fábrica.\nDisponibilidad constante, mejor margen y cero improvisación.',
        help_text='Texto descriptivo debajo del título',
    )
    hero_stat_1_value = models.CharField(max_length=20, blank=True, default='+2K')
    hero_stat_1_label = models.CharField(max_length=40, blank=True, default='Productos')
    hero_stat_2_value = models.CharField(max_length=20, blank=True, default='100%')
    hero_stat_2_label = models.CharField(max_length=40, blank=True, default='Stock real')

    class Meta:
        verbose_name = 'Configuración del sitio'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Configuración del sitio'


class HeroSlide(models.Model):
    MEDIA_IMAGE = 'image'
    MEDIA_VIDEO = 'video'
    MEDIA_CHOICES = [(MEDIA_IMAGE, 'Imagen'), (MEDIA_VIDEO, 'Video')]

    media_type    = models.CharField(max_length=10, choices=MEDIA_CHOICES, default=MEDIA_IMAGE)
    image         = models.ImageField(upload_to='hero/', blank=True)
    video         = models.FileField(upload_to='hero/videos/', blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active     = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order']
        verbose_name = 'Slide del Hero'
        verbose_name_plural = 'Slides del Hero'

    @property
    def file_url(self):
        if self.media_type == self.MEDIA_VIDEO:
            return self.video.url if self.video else None
        return self.image.url if self.image else None

    def delete_files(self):
        if self.image:
            self.image.delete(save=False)
        if self.video:
            self.video.delete(save=False)

    def __str__(self):
        return f'Slide #{self.display_order} [{self.media_type}] (pk={self.pk})'


class VolumeTier(models.Model):
    """Descuento por volumen ligado a una categoría."""
    category        = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='volume_tiers')
    min_qty         = models.PositiveIntegerField(help_text='Cantidad mínima para activar el descuento')
    discount_amount = models.DecimalField(max_digits=8, decimal_places=2,
                                          help_text='Descuento en MXN que se resta al precio final de cada producto')

    class Meta:
        ordering = ['min_qty']
        unique_together = ['category', 'min_qty']
        verbose_name = 'Tier de volumen'
        verbose_name_plural = 'Tiers de volumen'

    def __str__(self):
        return f'{self.category.name} — {self.min_qty}+ pzs → -${self.discount_amount}'


class SizeGroup(models.Model):
    name             = models.CharField(max_length=100)
    sizes            = models.JSONField()
    conversion_table = models.JSONField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Grupo de tallas'
        verbose_name_plural = 'Grupos de tallas'

    def __str__(self):
        return self.name


class Section(models.Model):
    """Agrupa subcategorías dentro de una categoría padre para organizar el catálogo."""
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name='sections',
        limit_choices_to={'parent__isnull': True},
    )
    categories = models.ManyToManyField(
        Category, related_name='in_sections', blank=True,
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Sección'
        verbose_name_plural = 'Secciones'

    def __str__(self):
        return f'{self.parent.name} / {self.name}'


class SubcategorySection(models.Model):
    """Agrupa productos dentro de una subcategoría para organizar la lista del catálogo."""
    name = models.CharField(max_length=100)
    subcategory = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='product_sections',
        limit_choices_to={'parent__isnull': False},
    )
    products = models.ManyToManyField(
        Product, related_name='in_subsections', blank=True,
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Sección de subcategoría'
        verbose_name_plural = 'Secciones de subcategoría'

    def __str__(self):
        return f'{self.subcategory.name} / {self.name}'
