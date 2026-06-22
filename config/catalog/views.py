import json
from decimal import Decimal, InvalidOperation

from django.db.models import DecimalField, ExpressionWrapper, F, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, render
from django.core.paginator import Paginator

from django.urls import reverse

from .models import Category, HeroSlide, Product, ProductImage, Section, SubcategorySection, Tag

# select_related mínimo para que los productos funcionen correctamente en las tarjetas
# (cascade de size_group y has_color_variants requieren relaciones precargadas)
_CARD_SELECT = (
    'category__parent',
    'category__size_group',
    'category__parent__size_group',
    'size_group',
)


def _category_cover(cat):
    if cat.image:
        return cat.image.url
    pi = ProductImage.objects.filter(product__category=cat, product__is_active=True).first()
    if not pi:
        pi = ProductImage.objects.filter(product__category__parent=cat, product__is_active=True).first()
    return pi.image.url if pi else None


def home(request):
    featured = list(
        Product.objects
        .filter(is_active=True, is_featured=True, images__isnull=False)
        .select_related(*_CARD_SELECT)
        .prefetch_related('images', 'tags')
        .distinct()
    )
    if len(featured) < 8:
        excluded = [p.pk for p in featured]
        rest = list(
            Product.objects
            .filter(is_active=True, images__isnull=False)
            .exclude(pk__in=excluded)
            .select_related(*_CARD_SELECT)
            .prefetch_related('images', 'tags')
            .order_by('-created_at')
            .distinct()[:8 - len(featured)]
        )
        new_products = featured + rest
    else:
        new_products = featured[:8]

    top_cats = list(Category.objects.filter(is_active=True, parent=None).order_by('display_order'))
    home_categories = [
        {'name': c.name, 'slug': c.slug, 'cover_url': _category_cover(c)}
        for c in top_cats
    ]

    hero_slides = list(HeroSlide.objects.filter(is_active=True).order_by('display_order'))

    return render(request, 'catalog/home.html', {
        'new_products':    new_products,
        'home_categories': home_categories,
        'hero_slides':     hero_slides,
    })


def catalog_hub(request):
    top_cats = Category.objects.filter(is_active=True, parent=None).order_by('display_order', 'name')
    categories = []
    for cat in top_cats:
        product_count = Product.objects.filter(
            Q(category=cat) | Q(category__parent=cat), is_active=True
        ).count()
        categories.append({
            'obj': cat,
            'cover_url': _category_cover(cat),
            'subcat_count': cat.subcategories.filter(is_active=True).count(),
            'product_count': product_count,
        })
    return render(request, 'catalog/hub.html', {'categories': categories})


def category_hub(request, cat_slug):
    parent = get_object_or_404(Category, slug=cat_slug, parent=None, is_active=True)

    cat_sections = (Section.objects
                    .filter(parent=parent, is_active=True)
                    .prefetch_related('categories')
                    .order_by('display_order', 'name'))

    if cat_sections.exists():
        assigned_pks = set()
        sections_data = []
        for sec in cat_sections:
            subcats = []
            for sub in sec.categories.filter(is_active=True).order_by('display_order', 'name'):
                assigned_pks.add(sub.pk)
                subcats.append({
                    'obj': sub,
                    'cover_url': _category_cover(sub),
                    'product_count': sub.products.filter(is_active=True).count(),
                })
            sections_data.append({'section': sec, 'subcats': subcats})

        unassigned = [
            {
                'obj': sub,
                'cover_url': _category_cover(sub),
                'product_count': sub.products.filter(is_active=True).count(),
            }
            for sub in parent.subcategories
                              .filter(is_active=True)
                              .exclude(pk__in=assigned_pks)
                              .order_by('display_order', 'name')
        ]
        return render(request, 'catalog/category_hub.html', {
            'parent': parent,
            'sections_data': sections_data,
            'unassigned': unassigned,
        })

    subcats_qs = parent.subcategories.filter(is_active=True).order_by('display_order', 'name')
    if not subcats_qs.exists():
        return product_list(request, cat_slug=cat_slug)

    subcategories = [
        {
            'obj': sub,
            'cover_url': _category_cover(sub),
            'product_count': sub.products.filter(is_active=True).count(),
        }
        for sub in subcats_qs
    ]
    return render(request, 'catalog/category_hub.html', {
        'parent': parent,
        'subcategories': subcategories,
    })


def _annotate_final(qs):
    # Debe coincidir con Product.final_price:
    #  - price_override fijo gana sobre todo (Coalesce externo);
    #  - margen y envío se toman de la categoría RAÍZ (category__parent); si el
    #    producto cuelga directo de una raíz, parent es NULL → cae a la propia.
    return qs.annotate(
        final_price_calc=ExpressionWrapper(
            Coalesce(
                F('price_override'),
                F('base_price')
                + Coalesce(
                    F('shipping_override'),
                    F('category__parent__shipping_cost'),
                    F('category__shipping_cost'),
                )
                + Coalesce(
                    F('category__parent__profit_margin'),
                    F('category__profit_margin'),
                ),
            ),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
    )


def _to_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _apply_filters(request, qs):
    selected_tags = request.GET.getlist('tag')
    if selected_tags:
        qs = qs.filter(tags__name__in=selected_tags).distinct()

    precio_min = _to_decimal(request.GET.get('precio_min'))
    precio_max = _to_decimal(request.GET.get('precio_max'))
    if precio_min is not None:
        qs = qs.filter(final_price_calc__gte=precio_min)
    if precio_max is not None:
        qs = qs.filter(final_price_calc__lte=precio_max)

    if request.GET.get('stock') == 'disponible':
        qs = qs.filter(status='available')

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(sku__icontains=q) | Q(category__name__icontains=q)
        ).distinct()

    orden = request.GET.get('orden', '')
    if orden == 'precio_asc':
        qs = qs.order_by('final_price_calc', 'id')
    elif orden == 'precio_desc':
        qs = qs.order_by('-final_price_calc', 'id')
    else:
        qs = qs.order_by('display_order', '-created_at', 'id')

    return qs, selected_tags, q


def _build_page_range(page_obj, paginator):
    """Return list of page numbers with None as ellipsis sentinel.
    Always includes first 2, last 2, and a window of current ±2."""
    current = page_obj.number
    total   = paginator.num_pages
    if total <= 9:
        return list(range(1, total + 1))
    pages = set()
    pages.update([1, 2])
    pages.update([total - 1, total])
    pages.update(range(max(1, current - 2), min(total + 1, current + 3)))
    result, prev = [], None
    for p in sorted(pages):
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result


def _paginate(request, qs):
    _MAP = {'12': 12, '50': 50, '100': 100}
    param = request.GET.get('per_page', '50')
    if param not in _MAP:
        param = '50'
    paginator = Paginator(qs, _MAP[param])
    page_obj  = paginator.get_page(request.GET.get('page'))
    return page_obj, paginator, param, _build_page_range(page_obj, paginator)


_PRICE_RANGES = [
    {'label': '– $200',         'min': '',     'max': '200'},
    {'label': '$200 – $500',    'min': '200',  'max': '500'},
    {'label': '$500 – $1,000',  'min': '500',  'max': '1000'},
    {'label': '$1,000 – $2,000','min': '1000', 'max': '2000'},
    {'label': '$2,000 – $3,000','min': '2000', 'max': '3000'},
]


def _has_active_filters(request, selected_tags):
    return bool(
        selected_tags
        or request.GET.get('precio_min')
        or request.GET.get('precio_max')
        or request.GET.get('stock')
        or request.GET.get('q', '').strip()
        or request.GET.get('orden')
    )


def product_list(request, cat_slug, subcat_slug=None):
    parent = get_object_or_404(Category, slug=cat_slug, parent=None, is_active=True)
    subcat = None
    active_section = None
    active_subsection = None

    if subcat_slug:
        subcat = get_object_or_404(Category, slug=subcat_slug, parent=parent, is_active=True)
        if request.GET.get('subseccion', '').isdigit():
            active_subsection = get_object_or_404(
                SubcategorySection, pk=int(request.GET['subseccion']),
                subcategory=subcat, is_active=True,
            )
            prod_pks = active_subsection.products.values_list('pk', flat=True)
            qs = Product.objects.filter(is_active=True, pk__in=prod_pks, category=subcat)
        else:
            qs = Product.objects.filter(is_active=True, category=subcat)
    elif request.GET.get('seccion', '').isdigit():
        active_section = get_object_or_404(
            Section, pk=int(request.GET['seccion']), parent=parent, is_active=True
        )
        sub_pks = active_section.categories.values_list('pk', flat=True)
        qs = Product.objects.filter(is_active=True, category__pk__in=sub_pks)
    else:
        qs = Product.objects.filter(
            Q(category=parent) | Q(category__parent=parent), is_active=True
        ).distinct()

    qs = qs.select_related(*_CARD_SELECT).prefetch_related('images', 'tags', 'variants')
    qs = _annotate_final(qs)
    qs, selected_tags, q = _apply_filters(request, qs)

    # Vista por secciones: categoría padre sin filtros y sin subcat/seccion activa
    sections = None
    if subcat and not active_subsection and not _has_active_filters(request, selected_tags):
        subsections_qs = (
            SubcategorySection.objects
            .filter(subcategory=subcat, is_active=True)
            .prefetch_related('products')
            .order_by('display_order', 'name')
        )
        if subsections_qs.exists():
            sections = []
            assigned_pks = set()
            for subsec in subsections_qs:
                sec_prods = list(
                    subsec.products
                    .filter(is_active=True)
                    .select_related(*_CARD_SELECT)
                    .prefetch_related('images', 'tags', 'variants')
                    .order_by('display_order', '-created_at')
                )
                if not sec_prods:
                    continue
                assigned_pks.update(p.pk for p in sec_prods)
                total = len(sec_prods)
                ver_url = (
                    reverse('catalog:product_list', args=[cat_slug, subcat_slug])
                    + f'?subseccion={subsec.pk}'
                    if total > 8 else None
                )
                sections.append({
                    'label':    subsec.name,
                    'ver_url':  ver_url,
                    'products': sec_prods[:8],
                    'total':    total,
                })
            unassigned = list(
                Product.objects
                .filter(is_active=True, category=subcat)
                .exclude(pk__in=assigned_pks)
                .select_related(*_CARD_SELECT)
                .prefetch_related('images', 'tags', 'variants')
                .order_by('display_order', '-created_at')
            )
            if unassigned:
                sections.append({
                    'label':    'Otros',
                    'ver_url':  None,
                    'products': unassigned[:8],
                    'total':    len(unassigned),
                })
            if not sections:
                sections = None
    elif not subcat and not active_section and not _has_active_filters(request, selected_tags):
        cat_sections = (Section.objects
                        .filter(parent=parent, is_active=True)
                        .prefetch_related('categories')
                        .order_by('display_order', 'name'))
        if cat_sections.exists():
            sections = []
            for sec in cat_sections:
                sub_pks = list(sec.categories.values_list('pk', flat=True))
                if not sub_pks:
                    continue
                sec_qs = (
                    Product.objects
                    .filter(is_active=True, category__pk__in=sub_pks)
                    .select_related(*_CARD_SELECT)
                    .prefetch_related('images', 'tags', 'variants')
                    .order_by('display_order', '-created_at')
                )
                total = sec_qs.count()
                if total:
                    ver_url = (
                        reverse('catalog:category', args=[cat_slug]) + f'?seccion={sec.pk}'
                        if total > 8 else None
                    )
                    sections.append({
                        'label':    sec.name,
                        'ver_url':  ver_url,
                        'products': list(sec_qs[:8]),
                        'total':    total,
                    })
        else:
            subcats_qs = parent.subcategories.filter(is_active=True).order_by('display_order', 'name')
            if subcats_qs.exists():
                sections = []
                for sub in subcats_qs:
                    sub_qs = (
                        Product.objects
                        .filter(is_active=True, category=sub)
                        .select_related(*_CARD_SELECT)
                        .prefetch_related('images', 'tags', 'variants')
                        .order_by('display_order', '-created_at')
                    )
                    total = sub_qs.count()
                    if total:
                        ver_url = (
                            reverse('catalog:product_list', args=[cat_slug, sub.slug])
                            if total > 8 else None
                        )
                        sections.append({
                            'label':    sub.name,
                            'ver_url':  ver_url,
                            'products': list(sub_qs[:8]),
                            'total':    total,
                        })

    page_obj = paginator = per_page = page_range = None
    flat_products = []
    if sections is None:
        page_obj, paginator, per_page, page_range = _paginate(request, qs)
        flat_products = page_obj.object_list

    return render(request, 'catalog/list.html', {
        'products':           flat_products,
        'page_obj':           page_obj,
        'paginator':          paginator,
        'page_range':         page_range,
        'parent':             parent,
        'subcat':             subcat,
        'active_section':     active_section,
        'active_subsection':  active_subsection,
        'sections':           sections,
        'all_tags':           Tag.objects.all(),
        'selected_tags':      selected_tags,
        'q':                  q,
        'per_page':           per_page,
        'price_ranges':       _PRICE_RANGES,
    })


def search_results(request):
    qs = (
        Product.objects
        .filter(is_active=True)
        .select_related(*_CARD_SELECT)
        .prefetch_related('images', 'tags', 'variants')
    )
    qs = _annotate_final(qs)
    qs, selected_tags, q = _apply_filters(request, qs)
    page_obj, paginator, per_page, page_range = _paginate(request, qs)

    return render(request, 'catalog/list.html', {
        'products':     page_obj.object_list,
        'page_obj':     page_obj,
        'paginator':    paginator,
        'page_range':   page_range,
        'parent':       None,
        'subcat':       None,
        'all_tags':     Tag.objects.all(),
        'selected_tags': selected_tags,
        'q':            q,
        'per_page':     per_page,
        'price_ranges': _PRICE_RANGES,
    })


def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects
        .select_related(
            'category__parent',
            'category__size_group',
            'category__parent__size_group',
            'size_group',           # per-product override
        )
        .prefetch_related('images', 'tags', 'variants'),
        pk=pk, is_active=True
    )
    related_products = (
        Product.objects
        .filter(category=product.category, is_active=True)
        .exclude(pk=product.pk)
        .prefetch_related('images', 'tags')
        [:4]
    )
    variants_data = [
        {
            'pk':          v.pk,
            'name':        v.name,
            'attributes':  v.attributes,
            'extra_price': float(v.extra_price),
            'final_price': float(v.final_price),
            'stock':       v.stock,
            'is_active':   v.is_active,
        }
        for v in product.variants.filter(is_active=True)
    ]
    base_price = float(product.final_price)
    root       = product.category.parent if product.category.parent_id else product.category
    tiers      = list(root.volume_tiers.values('min_qty', 'discount_amount').order_by('min_qty'))
    tiers_data = [
        {'min_qty': t['min_qty'], 'discount': float(t['discount_amount']),
         'price': max(0.0, base_price - float(t['discount_amount']))}
        for t in tiers
    ]
    qty_step = int(root.min_qty_per_item) if root.min_qty_per_item > 0 else 1

    # Size group: producto > subcategoría > categoría padre (cascade)
    cat        = product.category
    size_group = product.size_group or cat.size_group or root.size_group

    # Deduplicar tallas — evita filas duplicadas si el grupo tiene el mismo valor repetido
    unique_sizes = list(dict.fromkeys(size_group.sizes)) if size_group else []
    sizes_json   = json.dumps(unique_sizes) if unique_sizes else 'null'
    conv_json    = json.dumps(size_group.conversion_table) if (size_group and size_group.conversion_table) else 'null'

    # Modo colorway: producto > subcategoría > categoría padre (cualquiera activa el modo)
    all_images = list(product.images.all())  # usa la caché del prefetch

    # has_colorway: colorway está activo en ALGÚN nivel de la jerarquía
    has_colorway = (
        (product.has_color_variants or cat.has_color_variants or root.has_color_variants)
        and len(all_images) > 1
    )
    # color_variant_mode: UI exclusivo de colorway (sin selector de tallas)
    color_variant_mode = not size_group and has_colorway

    # images_data se pasa siempre que haya colorway activo.
    # Cuando el producto tiene AMBOS (tallas + colorway), el frontend usa
    # la imagen seleccionada en el thumbnail para diferenciar items del carrito.
    images_data = [
        {
            'pk':          img.pk,
            'url':         img.image.url,
            'is_cover':    img.is_cover,
            'color_label': img.color_label,
        }
        for img in all_images
    ] if has_colorway else []

    variant_colors = product.variant_colors or []

    return render(request, 'catalog/detail.html', {
        'product':             product,
        'related_products':    related_products,
        'variants_json':       json.dumps(variants_data),
        'tiers':               tiers_data,
        'tiers_json':          json.dumps(tiers_data),
        'base_final_price':    base_price,
        'qty_step':            qty_step,
        'size_group':          size_group,
        'unique_sizes':        unique_sizes,
        'sizes_json':          sizes_json,
        'conv_json':           conv_json,
        'has_colorway':        has_colorway,
        'color_variant_mode':  color_variant_mode,
        'images_json':         json.dumps(images_data),
        'variant_colors':      variant_colors,
        'variant_colors_json': json.dumps(variant_colors),
    })


def nosotros(request):
    return render(request, 'catalog/nosotros.html')


def terminos(request):
    return render(request, 'catalog/terminos.html')


def privacidad(request):
    return render(request, 'catalog/privacidad.html')
