from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from catalog.models import Category, Product, HeroSlide, SiteConfig, Section
from api.serializers import (
    CategorySerializer, CategoryDetailSerializer,
    ProductListSerializer, ProductDetailSerializer,
    HeroSlideSerializer, SiteConfigSerializer,
)


@api_view(['GET'])
@permission_classes([AllowAny])
def home(request):
    config = SiteConfig.get()
    slides = HeroSlide.objects.filter(is_active=True)
    featured = (
        Product.objects
        .filter(is_active=True, is_featured=True)
        .prefetch_related('images', 'category__parent')[:8]
    )
    categories = (
        Category.objects
        .filter(is_active=True, parent__isnull=True)
        .order_by('display_order', 'name')
    )
    ctx = {'request': request}
    return Response({
        'config': SiteConfigSerializer(config, context=ctx).data,
        'hero_slides': HeroSlideSerializer(slides, many=True, context=ctx).data,
        'featured_products': ProductListSerializer(featured, many=True, context=ctx).data,
        'categories': CategorySerializer(categories, many=True, context=ctx).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def categories(request):
    qs = (
        Category.objects
        .filter(is_active=True, parent__isnull=True)
        .order_by('display_order', 'name')
    )
    return Response(CategorySerializer(qs, many=True, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def category_detail(request, cat_slug):
    try:
        cat = Category.objects.get(slug=cat_slug, is_active=True)
    except Category.DoesNotExist:
        return Response({'detail': 'Categoría no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
    return Response(CategoryDetailSerializer(cat, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def product_list(request, cat_slug, subcat_slug):
    try:
        parent = Category.objects.get(slug=cat_slug, is_active=True, parent__isnull=True)
        subcat = Category.objects.get(slug=subcat_slug, parent=parent, is_active=True)
    except Category.DoesNotExist:
        return Response({'detail': 'Categoría no encontrada.'}, status=status.HTTP_404_NOT_FOUND)

    qs = Product.objects.filter(is_active=True, category=subcat).prefetch_related('images', 'category__parent')

    # Filters
    tags = request.query_params.getlist('tags')
    if tags:
        qs = qs.filter(tags__name__in=tags).distinct()

    min_price = request.query_params.get('min_price')
    max_price = request.query_params.get('max_price')
    if min_price:
        qs = qs.filter(base_price__gte=min_price)
    if max_price:
        qs = qs.filter(base_price__lte=max_price)

    in_stock = request.query_params.get('in_stock')
    if in_stock == '1':
        qs = qs.filter(status='available')

    ordering = request.query_params.get('ordering', '-created_at')
    allowed_orderings = {'created_at', '-created_at', 'base_price', '-base_price', 'name', '-name'}
    if ordering not in allowed_orderings:
        ordering = '-created_at'
    qs = qs.order_by(ordering)

    # Pagination
    try:
        per_page = max(1, min(int(request.query_params.get('per_page', 24)), 96))
        page = max(1, int(request.query_params.get('page', 1)))
    except (ValueError, TypeError):
        per_page, page = 24, 1

    total = qs.count()
    start = (page - 1) * per_page
    items = qs[start:start + per_page]

    ctx = {'request': request}
    return Response({
        'results': ProductListSerializer(items, many=True, context=ctx).data,
        'count': total,
        'page': page,
        'per_page': per_page,
        'num_pages': max(1, (total + per_page - 1) // per_page),
        'category': CategorySerializer(parent, context=ctx).data,
        'subcategory': CategorySerializer(subcat, context=ctx).data,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def product_detail(request, pk):
    try:
        product = (
            Product.objects
            .filter(is_active=True)
            .prefetch_related('images', 'variants', 'tags', 'category__parent')
            .get(pk=pk)
        )
    except Product.DoesNotExist:
        return Response({'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND)
    return Response(ProductDetailSerializer(product, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def search(request):
    q = request.query_params.get('q', '').strip()
    if not q:
        return Response({'results': [], 'count': 0})

    qs = (
        Product.objects
        .filter(is_active=True)
        .filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(category__name__icontains=q))
        .prefetch_related('images', 'category__parent')
        .distinct()[:48]
    )
    ctx = {'request': request}
    return Response({
        'results': ProductListSerializer(qs, many=True, context=ctx).data,
        'count': qs.count(),
        'query': q,
    })
