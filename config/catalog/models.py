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
    base_price_override = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text='Costo del proveedor para TODOS los productos de esta subcategoría. '
                  'Vacío = usa el base_price individual de cada producto.'
    )
    profit_margin_override = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text='Ganancia para esta subcategoría. Vacío = usa la ganancia de la categoría raíz.'
    )
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

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        # Los overrides solo los lee la categoría DIRECTA del producto
        # (Product.effective_base_price / effective_profit_margin). En una raíz
        # con subcategorías serían un no-op silencioso para los productos de
        # las subcategorías — rechazar en vez de guardar algo que no aplica.
        tiene_override = (
            self.base_price_override is not None
            or self.profit_margin_override is not None
        )
        if (tiene_override and self.parent_id is None and self.pk
                and self.subcategories.exists()):
            raise ValidationError({
                'base_price_override':
                    'Esta categoría raíz tiene subcategorías: el override no '
                    'aplicaría a sus productos. Configúralo en cada subcategoría.',
            })

        # El descuento por volumen se resta del precio final y sale de la
        # ganancia. Los tiers viven en la raíz y son un monto fijo en pesos,
        # dimensionado para el margen de esa raíz; una ganancia más chica aquí
        # haría que a esa cantidad el precio caiga por debajo del costo.
        # Pasó de verdad: Alfileres (20) bajo Gorra (tier −70) → pedidos en $0.
        if self.profit_margin_override is not None:
            raiz = self.parent if self.parent_id else self
            if raiz and raiz.pk:
                mayor = raiz.volume_tiers.order_by('-discount_amount').first()
                if mayor and self.profit_margin_override < mayor.discount_amount:
                    raise ValidationError({
                        'profit_margin_override':
                            f'La ganancia ({self.profit_margin_override}) es menor '
                            f'que el mayor descuento por volumen de {raiz.name} '
                            f'({mayor.discount_amount}, desde {mayor.min_qty} pzs): '
                            f'a esa cantidad el precio quedaría por debajo del '
                            f'costo. Sube la ganancia o ajusta el tier.',
                    })

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
    # Último precio visto en Modaverse. Sirve para distinguir un precio
    # automático (base_price == modaverse_price) de uno editado a mano en el
    # panel, y así sincronizar sin pisar ediciones manuales.
    modaverse_price = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text='Último precio del proveedor. Si difiere de base_price, el precio fue editado a mano.'
    )

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
    def effective_base_price(self):
        """Costo del proveedor: override de la subcategoría (o raíz directa) si
        está puesto, si no el base_price individual del producto."""
        cat = self.category
        if cat.base_price_override is not None:
            return cat.base_price_override
        bp = self.base_price
        return bp if isinstance(bp, Decimal) else Decimal(str(bp))

    @property
    def effective_profit_margin(self):
        """Ganancia: override de la subcategoría (o raíz directa) si está
        puesto, si no la ganancia de la categoría raíz."""
        cat = self.category
        if cat.profit_margin_override is not None:
            return cat.profit_margin_override
        return self._root_category.profit_margin

    @property
    def final_price(self):
        if self.price_override is not None:
            return self.price_override
        return self.effective_base_price + self.effective_shipping + self.effective_profit_margin

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


def next_sku_index(prefix: str) -> int:
    """Siguiente índice libre para 'RYL-{prefix}-'.

    Considera tanto el catálogo (Product) como los SKUs ya reservados por
    pendientes sin aprobar: si solo mirara Product, dos corridas de
    load_productos antes de aprobar reasignarían el mismo SKU y al aprobar el
    segundo pendiente se pisaría el producto del primero.
    """
    nums = []
    reserved = list(
        Product.objects.filter(sku__startswith=f'RYL-{prefix}-')
        .values_list('sku', flat=True)
    )
    reserved += [
        s for s in PendingProduct.objects
        .values_list('raw_data__sku', flat=True)
        if isinstance(s, str) and s.startswith(f'RYL-{prefix}-')
    ]
    for sku in reserved:
        try:
            nums.append(int(sku.rsplit('-', 1)[-1]))
        except (ValueError, IndexError):
            pass
    return (max(nums) + 1) if nums else 1


def free_sku(prefix: str) -> str:
    """SKU completo libre para el prefijo dado."""
    idx = next_sku_index(prefix)
    while Product.objects.filter(sku=f'RYL-{prefix}-{idx:03d}').exists():
        idx += 1
    return f'RYL-{prefix}-{idx:03d}'


def _prefix_from_sku(sku: str) -> str:
    """'RYL-GOR-012' → 'GOR'. Devuelve '' si el SKU no tiene ese formato."""
    base = sku.rpartition('-')[0]
    return base[4:] if base.startswith('RYL-') else ''


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

    @property
    def final_price(self):
        """Precio estimado de venta: respeta los mismos overrides de subcategoría
        que Product.final_price, para que el estimado antes de aprobar sea
        consistente con el precio real una vez aprobado."""
        if not self.category:
            return self.base_price + Decimal('100')
        root = self.category.parent if self.category.parent_id else self.category
        bp = (
            self.category.base_price_override
            if self.category.base_price_override is not None
            else self.base_price
        )
        margin = (
            self.category.profit_margin_override
            if self.category.profit_margin_override is not None
            else root.profit_margin
        )
        return bp + root.shipping_cost + margin

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

        # La identidad del producto es su supplier_url, no su SKU: el SKU es
        # solo un correlativo interno y puede haber quedado reservado por otro
        # producto. Buscar por supplier_url mantiene approve() idempotente.
        product = Product.objects.filter(supplier_url=self.supplier_url).first() \
            if self.supplier_url else None

        if product is None:
            # Si el SKU reservado ya lo tomó otro producto, tomar uno libre en
            # vez de pisarlo (antes: get_or_create(sku=...) sobrescribía el ajeno).
            if not sku or Product.objects.filter(sku=sku).exists():
                prefix = _prefix_from_sku(sku) or 'GEN'
                sku = free_sku(prefix)
            product = Product.objects.create(
                sku=sku,
                name=self.display_name,
                modaverse_name=self.modaverse_name or self.display_name,
                category=self.category,
                base_price=self.base_price,
                # nace como precio automático: base_price == modaverse_price
                modaverse_price=self.base_price,
                supplier_url=self.supplier_url,
                variant_colors=variant_colors,
                size_group=size_group,
                description=description,
                status='available',
                is_active=True,
            )
        else:
            product.name            = self.display_name
            product.modaverse_name  = self.modaverse_name or self.display_name
            product.base_price      = self.base_price
            product.modaverse_price = self.base_price
            product.save(update_fields=[
                'name', 'modaverse_name', 'base_price', 'modaverse_price',
            ])

        if self.cover_image and not product.images.filter(is_cover=True).exists():
            import shutil, os
            from django.conf import settings
            src = self.cover_image.path
            if os.path.exists(src):
                ext = os.path.splitext(src)[1]
                dst_rel = f'products/{product.sku}{ext}'
                dst_abs = os.path.join(settings.MEDIA_ROOT, dst_rel)
                os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
                shutil.copy2(src, dst_abs)
                ProductImage.objects.create(product=product, image=dst_rel, is_cover=True)

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


class TipoArticulo(models.Model):
    nombre   = models.CharField(max_length=100)
    keywords = models.TextField(
        help_text='Palabras clave separadas por coma. Ej: gorra,cap,ny,la,za'
    )
    costo    = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'Tipo de artículo'
        verbose_name_plural = 'Tipos de artículo'

    def __str__(self):
        return self.nombre

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        # matches() parte las keywords SOLO por coma: un bloque con saltos de
        # línea se vuelve una sola keyword gigante que nunca matchea → la
        # venta se registra con costo $0 sin que nadie lo note.
        if '\n' in self.keywords or '\r' in self.keywords:
            raise ValidationError({
                'keywords': 'Separa las keywords con COMAS, no con saltos de '
                            'línea. Ej: sudadera G5, Sud G5, s G5',
            })
        # Dos tipos con la misma keyword exacta compiten en el matching por
        # orden alfabético del tipo, no por especificidad — una keyword
        # duplicada asigna el costo del tipo equivocado sin avisar (caso
        # real: pedido #39, 'new' en dos tipos a la vez).
        mis_keywords = {' '.join(kw.split()).lower() for kw in self.keywords_list}
        for otro in TipoArticulo.objects.exclude(pk=self.pk):
            for kw in otro.keywords_list:
                kw_norm = ' '.join(kw.split()).lower()
                if kw_norm in mis_keywords:
                    raise ValidationError({
                        'keywords': f'La keyword "{kw_norm}" ya está en uso por '
                                    f'"{otro.nombre}" — dos tipos no pueden compartir '
                                    'la misma keyword exacta (usa una más específica).',
                    })

    def matches(self, texto: str) -> bool:
        """True si alguna keyword aparece en texto (case-insensitive)."""
        texto = ' '.join(texto.lower().split())
        return any(' '.join(kw.split()).lower() in texto for kw in self.keywords.split(',') if kw.strip())

    @property
    def keywords_list(self) -> list[str]:
        return [kw.strip() for kw in self.keywords.split(',') if kw.strip()]


class AliasTexto(models.Model):
    """Texto de venta EXACTO → TipoArticulo.

    Las keywords matchean por substring, así que la pantalla de tipos exige
    que la keyword esté contenida en el texto tecleado. Eso deja textos que
    ninguna keyword puede capturar: `Jordan` no contiene `jordan 4`, y
    `jordan` a secas se robaría las ventas de `jordan 1` por el orden
    alfabético del matcher. El alias es coincidencia exacta, así que no puede
    filtrarse a ningún otro texto — por eso es lo que se crea cuando alguien
    elige una opción desde el grupo.
    """

    texto      = models.CharField(
        max_length=200, unique=True,
        help_text='Texto tal como se teclea. Se guarda en minúsculas y con espacios colapsados.')
    tipo       = models.ForeignKey(TipoArticulo, on_delete=models.CASCADE, related_name='alias')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['texto']
        verbose_name = 'Alias de texto'
        verbose_name_plural = 'Alias de texto'

    def __str__(self):
        return f'{self.texto} → {self.tipo.nombre}'

    def save(self, *args, **kwargs):
        # Import local: `catalog.services` importa modelos dentro de sus
        # funciones, y a nivel de módulo esto sería circular.
        from catalog.services import normalizar_texto
        self.texto = normalizar_texto(self.texto)
        super().save(*args, **kwargs)


class CodigoDescuento(models.Model):
    NEGOCIO = 'negocio'
    WEB     = 'web'
    AMBOS   = 'ambos'
    CANAL_CHOICES = [
        (AMBOS,   'Ambos (negocio + web)'),
        (NEGOCIO, 'Solo negocio (bot / POS)'),
        (WEB,     'Solo web (ryalsneackers.com)'),
    ]

    FIJO     = 'fijo'
    POR_ITEM = 'por_item'
    TIPO_DESCUENTO_CHOICES = [
        ('fijo',     'Fijo — descuenta un monto fijo del total'),
        ('por_item', 'Por ítem — se multiplica por cada ítem del alcance'),
    ]

    codigo         = models.CharField(max_length=50, unique=True, blank=True)
    descripcion    = models.CharField(max_length=200, blank=True,
                                      help_text='Para qué clientes o promoción es este código.')
    descuento      = models.DecimalField(max_digits=8, decimal_places=2,
                                         help_text='Monto en MXN. Fijo: se aplica al total. Por ítem: se multiplica por cada ítem del alcance.')
    tipo_descuento = models.CharField(
        max_length=10, choices=TIPO_DESCUENTO_CHOICES, default='fijo',
        help_text='Fijo: descuenta el monto sin importar cuántos ítems. Por ítem: multiplica el monto × cantidad de ítems del alcance.',
    )
    canal          = models.CharField(
        max_length=10, choices=CANAL_CHOICES, default=AMBOS,
        help_text='Dónde puede usarse este código.',
    )
    tipo_articulo  = models.ForeignKey(
        TipoArticulo, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='codigos',
        help_text='(Negocio) Dejar vacío para código global. Aplica solo a artículos del tipo seleccionado.',
    )
    categoria_web  = models.ForeignKey(
        'Category', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='codigos_descuento',
        help_text='(Web) Dejar vacío para código global. Aplica solo a productos de esta categoría.',
    )
    is_active      = models.BooleanField(default=True)
    usos_max       = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Máximo de usos. Dejar vacío para ilimitado.',
    )
    usos_actuales  = models.PositiveIntegerField(default=0)
    valid_hasta    = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['codigo']
        verbose_name = 'Código de descuento'
        verbose_name_plural = 'Códigos de descuento'

    @staticmethod
    def _generar_codigo():
        import secrets, string
        chars = string.ascii_uppercase + string.digits
        for _ in range(20):
            candidate = 'RY' + ''.join(secrets.choice(chars) for _ in range(8))
            if not CodigoDescuento.objects.filter(codigo=candidate).exists():
                return candidate
        raise ValueError('No se pudo generar un código único.')

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        if self.canal == self.WEB and self.tipo_articulo_id:
            errors['tipo_articulo'] = 'Los códigos de canal Web no pueden usar tipo de artículo (es exclusivo del negocio).'
        if self.canal == self.NEGOCIO and self.categoria_web_id:
            errors['categoria_web'] = 'Los códigos de canal Negocio no pueden tener categoría web.'
        if self.tipo_articulo_id and self.categoria_web_id:
            errors['categoria_web'] = 'No puedes combinar tipo de artículo y categoría web en el mismo código. Elige uno.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.codigo:
            self.codigo = self._generar_codigo()
        self.codigo = self.codigo.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        scope = self.tipo_articulo.nombre if self.tipo_articulo_id else (
            self.categoria_web.name if self.categoria_web_id else 'global'
        )
        return f'{self.codigo} — ${self.descuento} MXN ({self.canal} · {scope})'

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone
        return self.valid_hasta is not None and self.valid_hasta < timezone.localdate()

    @property
    def is_exhausted(self) -> bool:
        return self.usos_max is not None and self.usos_actuales >= self.usos_max

    @property
    def uso_pct(self) -> int:
        if not self.usos_max:
            return 0
        return min(100, round(self.usos_actuales * 100 / self.usos_max))
