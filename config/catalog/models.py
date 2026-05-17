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
    min_order_qty   = models.PositiveIntegerField(default=1, help_text='Mínimo de piezas por pedido para esta categoría')
    is_active       = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    banner_text   = models.TextField(blank=True)
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

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    tags = models.ManyToManyField(Tag, blank=True)
    supplier_url = models.URLField(blank=True, help_text='URL del producto en el proveedor')
    is_active   = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False, help_text='Aparece en "Nuevos ingresos" del inicio')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Producto'
        verbose_name_plural = 'Productos'
        ordering = ['-created_at']

    @property
    def effective_shipping(self):
        if self.shipping_override is not None:
            return self.shipping_override
        return self.category.shipping_cost

    @property
    def final_price(self):
        if self.price_override is not None:
            return self.price_override
        bp = self.base_price if isinstance(self.base_price, Decimal) else Decimal(str(self.base_price))
        return bp + self.effective_shipping + self.category.profit_margin

    @property
    def effective_min_qty(self):
        return max(self.min_order_qty, self.category.min_order_qty)

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


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images'
    )
    image = models.ImageField(upload_to='products/')
    is_cover = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)

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
