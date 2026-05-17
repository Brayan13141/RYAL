from rest_framework import serializers
from catalog.models import Category, Product, ProductImage, ProductVariant, Tag, HeroSlide, Section, SiteConfig
from orders.models import Order, OrderItem


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ['id', 'name', 'color_hex']


class CategorySerializer(serializers.ModelSerializer):
    cover_url = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'cover_url', 'display_order', 'banner_text']

    def get_cover_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class SubcategorySerializer(CategorySerializer):
    pass


class SectionSerializer(serializers.ModelSerializer):
    subcategories = SubcategorySerializer(source='categories', many=True)

    class Meta:
        model = Section
        fields = ['id', 'name', 'display_order', 'subcategories']


class CategoryDetailSerializer(CategorySerializer):
    subcategories = SubcategorySerializer(many=True)
    sections = SectionSerializer(many=True)

    class Meta(CategorySerializer.Meta):
        fields = CategorySerializer.Meta.fields + ['subcategories', 'sections']

    def get_subcategories(self, obj):
        qs = obj.subcategories.filter(is_active=True).order_by('display_order', 'name')
        return SubcategorySerializer(qs, many=True, context=self.context).data


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ['id', 'url', 'is_cover', 'display_order']

    def get_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class ProductVariantSerializer(serializers.ModelSerializer):
    final_price = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = ProductVariant
        fields = ['id', 'name', 'attributes', 'extra_price', 'final_price', 'stock', 'is_active']


class ProductListSerializer(serializers.ModelSerializer):
    cover_url = serializers.SerializerMethodField()
    final_price = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True)
    parent_slug = serializers.SerializerMethodField()
    min_qty = serializers.IntegerField(source='effective_min_qty', read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'sku', 'name', 'final_price', 'cover_url',
            'status', 'is_featured', 'category_name', 'category_slug',
            'parent_slug', 'min_qty',
        ]

    def get_cover_url(self, obj):
        request = self.context.get('request')
        img = obj.cover_image
        if img and request:
            return request.build_absolute_uri(img.image.url)
        return None

    def get_parent_slug(self, obj):
        if obj.category.parent:
            return obj.category.parent.slug
        return obj.category.slug


class ProductDetailSerializer(ProductListSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    category = CategorySerializer(read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + [
            'description', 'base_price', 'images', 'variants', 'tags', 'category',
        ]


class HeroSlideSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = HeroSlide
        fields = ['id', 'media_type', 'file_url', 'display_order']

    def get_file_url(self, obj):
        request = self.context.get('request')
        url = obj.file_url
        if url and request:
            return request.build_absolute_uri(url)
        return None


class SiteConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteConfig
        fields = [
            'whatsapp', 'track_message',
            'hero_eyebrow', 'hero_title_em', 'hero_title_strong', 'hero_sub',
            'hero_stat_1_value', 'hero_stat_1_label',
            'hero_stat_2_value', 'hero_stat_2_label',
        ]


class OrderItemSerializer(serializers.ModelSerializer):
    subtotal = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            'id', 'sku_snapshot', 'name_snapshot', 'variant_snapshot',
            'quantity', 'price_snapshot', 'subtotal',
        ]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    total = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Order
        fields = [
            'order_code', 'tracking_token', 'customer_name', 'customer_phone',
            'status', 'status_display', 'created_at', 'total', 'items',
        ]
