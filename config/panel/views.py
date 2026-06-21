import csv
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode as _urlencode

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from catalog.models import Category, HeroSlide, PendingProduct, Product, ProductImage, Section, SiteConfig, SizeGroup, SubcategorySection, VolumeTier
from orders.models import Order, OrderItem, SupplierOrder, SupplierOrderItem

_LOGIN = '/accounts/login/'
_UNSET = object()


def _staff(view):
    return staff_member_required(view, login_url=_LOGIN)


# Magic-byte signatures for allowed image formats
_IMAGE_MAGIC = (
    b'\xff\xd8\xff',                          # JPEG
    b'\x89PNG\r\n\x1a\n',                     # PNG
    b'GIF87a', b'GIF89a',                     # GIF
)
_WEBP_MAGIC = (b'RIFF', b'WEBP')             # checked separately (bytes 0-3 + 8-11)
_MAX_IMAGE_BYTES = 15 * 1024 * 1024          # 15 MB
_MAX_VIDEO_BYTES = 200 * 1024 * 1024         # 200 MB


def _validate_image_upload(f):
    """Return error string or None. Checks size and magic bytes."""
    if f.size > _MAX_IMAGE_BYTES:
        return f'Imagen demasiado grande (máx. 15 MB por archivo)'
    header = f.read(12)
    f.seek(0)
    if any(header.startswith(sig) for sig in _IMAGE_MAGIC):
        return None
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return None
    return 'Tipo de archivo no permitido (se aceptan JPEG, PNG, GIF, WebP)'


def _validate_video_upload(f):
    """Return error string or None. Checks size and magic bytes for common video formats."""
    if f.size > _MAX_VIDEO_BYTES:
        return 'Video demasiado grande (máx. 200 MB)'
    header = f.read(16)
    f.seek(0)
    # WebM / MKV — EBML header
    if header[:4] == b'\x1aE\xdf\xa3':
        return None
    # MP4 / MOV — ISO Base Media File Format (ftyp box at offset 4)
    if header[4:8] == b'ftyp':
        return None
    # AVI — RIFF container
    if header[:4] == b'RIFF' and header[8:12] == b'AVI ':
        return None
    return 'Tipo de video no válido (se aceptan MP4, WebM, MKV, AVI, MOV)'


def _apply_product_filters(qs, q='', cat='', active='', no_image=''):
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(sku__icontains=q) | Q(category__name__icontains=q)
        ).distinct()
    if cat:
        qs = qs.filter(
            Q(category__slug=cat) | Q(category__parent__slug=cat)
        ).distinct()
    if active == '1':
        qs = qs.filter(is_active=True)
    elif active == '0':
        qs = qs.filter(is_active=False)
    if no_image == '1':
        qs = qs.filter(images__isnull=True)
    return qs


# ─── Home config ─────────────────────────────────────────────────────────────

@_staff
def home_config(request):
    config = SiteConfig.get()

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'config':
            config.whatsapp = request.POST.get('whatsapp', '').strip() or config.whatsapp
            config.track_message = request.POST.get('track_message', '').strip()
            config.save()

        elif action == 'hero':
            config.hero_eyebrow     = request.POST.get('hero_eyebrow', '').strip()
            config.hero_title_em    = request.POST.get('hero_title_em', '').strip()
            config.hero_title_strong = request.POST.get('hero_title_strong', '').strip()
            config.hero_sub         = request.POST.get('hero_sub', '').strip()
            config.hero_stat_1_value = request.POST.get('hero_stat_1_value', '').strip()
            config.hero_stat_1_label = request.POST.get('hero_stat_1_label', '').strip()
            config.hero_stat_2_value = request.POST.get('hero_stat_2_value', '').strip()
            config.hero_stat_2_label = request.POST.get('hero_stat_2_label', '').strip()
            config.save()

        elif action == 'add_featured':
            pk = request.POST.get('product_pk', '')
            if pk.isdigit():
                Product.objects.filter(pk=int(pk)).update(is_featured=True)

        elif action == 'remove_featured':
            pk = request.POST.get('product_pk', '')
            if pk.isdigit():
                Product.objects.filter(pk=int(pk)).update(is_featured=False)

        return redirect('panel:home_config')

    q        = request.GET.get('q', '').strip()
    featured = (Product.objects.filter(is_featured=True)
                .select_related('category').prefetch_related('images')
                .order_by('name'))
    results  = []
    if q:
        results = (Product.objects
                   .filter(is_active=True, is_featured=False)
                   .filter(
                       Q(name__icontains=q) |
                       Q(sku__icontains=q)  |
                       Q(category__name__icontains=q)
                   )
                   .select_related('category').prefetch_related('images')
                   .distinct()[:20])

    top_cats = (Category.objects
                .filter(parent=None)
                .order_by('display_order', 'name'))

    hero_slides_all = HeroSlide.objects.order_by('display_order')

    return render(request, 'panel/home_config.html', {
        'config':          config,
        'featured':        featured,
        'results':         results,
        'q':               q,
        'top_cats':        top_cats,
        'hero_slides_all': hero_slides_all,
    })


# ─── Dashboard ───────────────────────────────────────────────────────────────

_ITEM_REVENUE = ExpressionWrapper(
    F('price_snapshot') * F('quantity'),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


def _stats(order_qs):
    """Revenue + units for a queryset of orders — 1 aggregate query."""
    result = (
        OrderItem.objects
        .filter(order__in=order_qs)
        .aggregate(revenue=Sum(_ITEM_REVENUE), units=Sum('quantity'))
    )
    return float(result['revenue'] or 0), int(result['units'] or 0)


@_staff
def dashboard(request):
    now    = timezone.now()
    hoy    = now.replace(hour=0, minute=0, second=0, microsecond=0)
    semana = hoy - timedelta(days=6)
    mes    = hoy.replace(day=1)

    base = Order.objects.exclude(status='cancelled')
    rev_hoy,    u_hoy    = _stats(base.filter(created_at__gte=hoy))
    rev_semana, u_semana = _stats(base.filter(created_at__gte=semana))
    rev_mes,    u_mes    = _stats(base.filter(created_at__gte=mes))

    # Gráfica últimos 7 días — 1 query agrupada por día en lugar de 7 queries
    chart_raw = (
        OrderItem.objects
        .filter(order__in=base.filter(created_at__gte=semana))
        .annotate(day=TruncDate('order__created_at'))
        .values('day')
        .annotate(revenue=Sum(_ITEM_REVENUE), units=Sum('quantity'))
    )
    chart_by_day = {row['day']: row for row in chart_raw}

    chart_labels = []
    chart_revenue = []
    chart_profit = []
    for i in range(6, -1, -1):
        day  = (hoy - timedelta(days=i)).date()
        data = chart_by_day.get(day, {})
        chart_labels.append(day.strftime('%d/%m'))
        chart_revenue.append(round(float(data.get('revenue') or 0)))
        chart_profit.append(int(data.get('units') or 0) * 100)

    # 1 query instead of N queries (one per status)
    _status_counts = dict(
        Order.objects.values('status').annotate(n=Count('pk')).values_list('status', 'n')
    )
    counts = {
        s: {'label': lbl, 'n': _status_counts.get(s, 0)}
        for s, lbl in Order.STATUS_CHOICES
    }
    recent            = Order.objects.prefetch_related('items').order_by('-created_at')[:8]
    p_active          = Product.objects.filter(is_active=True).count()
    p_inactive        = Product.objects.filter(is_active=False).count()
    p_no_image        = Product.objects.filter(images__isnull=True).count()
    p_active_no_image = Product.objects.filter(is_active=True, images__isnull=True).count()

    return render(request, 'panel/dashboard.html', {
        'counts':             counts,
        'recent':             recent,
        'p_active':           p_active,
        'p_inactive':         p_inactive,
        'p_no_image':         p_no_image,
        'p_active_no_image':  p_active_no_image,
        'total_orders':       Order.objects.count(),
        'rev_hoy':            round(rev_hoy),
        'ganancia_hoy':       u_hoy * 100,
        'rev_semana':         round(rev_semana),
        'ganancia_semana':    u_semana * 100,
        'rev_mes':            round(rev_mes),
        'ganancia_mes':       u_mes * 100,
        'chart_labels':       json.dumps(chart_labels),
        'chart_revenue':      json.dumps(chart_revenue),
        'chart_profit':       json.dumps(chart_profit),
    })


# ─── Orders ──────────────────────────────────────────────────────────────────

@_staff
def orders_list(request):
    qs = Order.objects.select_related('user').prefetch_related('items')

    status = request.GET.get('status', '')
    if status:
        qs = qs.filter(status=status)

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(order_code__icontains=q) |
            Q(customer_name__icontains=q) |
            Q(customer_phone__icontains=q) |
            Q(customer_email__icontains=q)
        )

    paginator = Paginator(qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'panel/orders_list.html', {
        'page_obj':       page_obj,
        'status_filter':  status,
        'q':              q,
        'status_choices': Order.STATUS_CHOICES,
    })


@_staff
def orders_export(request):
    qs = (Order.objects
          .exclude(status='cancelled')
          .prefetch_related('items')
          .order_by('-created_at'))

    if s := request.GET.get('status', ''):
        qs = qs.filter(status=s)
    if q := request.GET.get('q', '').strip():
        qs = qs.filter(
            Q(order_code__icontains=q) | Q(customer_name__icontains=q) |
            Q(customer_phone__icontains=q)
        )

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="pedidos-ryal.csv"'
    response.write('﻿')  # BOM para Excel

    writer = csv.writer(response)
    writer.writerow(['Código', 'Fecha', 'Cliente', 'Teléfono', 'Estado',
                     'Productos', 'Total MXN', 'Ganancia MXN'])

    for order in qs:
        qty   = sum(i.quantity for i in order.items.all())
        total = sum(float(i.price_snapshot) * i.quantity for i in order.items.all())
        writer.writerow([
            order.order_code,
            order.created_at.strftime('%d/%m/%Y %H:%M'),
            order.customer_name,
            order.customer_phone,
            order.get_status_display(),
            qty,
            round(total),
            qty * 100,
        ])

    return response


@_staff
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product__images', 'items__variant'),
        pk=pk,
    )
    return render(request, 'panel/order_detail.html', {
        'order':          order,
        'status_choices': Order.STATUS_CHOICES,
    })


@_staff
@require_POST
def order_status_update(request, pk):
    order = get_object_or_404(Order, pk=pk)
    new   = request.POST.get('status', '')
    valid = [s for s, _ in Order.STATUS_CHOICES]
    if new in valid:
        order.status = new
        order.save(update_fields=['status', 'updated_at'])
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'ok':    True,
            'status': order.status,
            'label':  order.get_status_display(),
        })
    return redirect('panel:order_detail', pk=pk)


@_staff
@require_POST
def order_payment_update(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        deposit = Decimal(request.POST.get('deposit', '0') or '0')
        if deposit < 0:
            deposit = Decimal('0')
    except (InvalidOperation, TypeError):
        deposit = Decimal('0')
    order.deposit = deposit
    order.is_paid = request.POST.get('is_paid') == '1'
    order.save(update_fields=['deposit', 'is_paid', 'updated_at'])
    return JsonResponse({'ok': True})


@_staff
def order_ticket(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related('items'), pk=pk)
    return render(request, 'panel/order_ticket.html', {'order': order})


@_staff
def order_ticket_pdf(request, pk):
    from io import BytesIO
    from xhtml2pdf import pisa
    order = get_object_or_404(Order.objects.prefetch_related('items'), pk=pk)
    html = render_to_string('panel/order_ticket.html', {'order': order, 'pdf_mode': True}, request=request)
    buf = BytesIO()
    pisa.CreatePDF(html, dest=buf, encoding='utf-8')
    response = HttpResponse(buf.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="ticket-{order.order_code}.pdf"'
    return response


# ─── Products ─────────────────────────────────────────────────────────────────

@_staff
def products_list(request):
    q        = request.GET.get('q', '').strip()
    cat      = request.GET.get('cat', '')
    active   = request.GET.get('active', '')
    no_image = request.GET.get('no_image', '')

    qs = _apply_product_filters(
        Product.objects.select_related(
            'category__parent',
            'category__size_group',
            'category__parent__size_group',
            'size_group',
        ).prefetch_related('images'),
        q=q, cat=cat, active=active, no_image=no_image,
    )

    _PER_PAGE_MAP = {'50': 50, '100': 100, '200': 200, 'todos': 5000}
    per_page_param = request.GET.get('per_page', '50')
    if per_page_param not in _PER_PAGE_MAP:
        per_page_param = '50'
    per_page = _PER_PAGE_MAP[per_page_param]

    total_count    = qs.count()
    no_image_count = Product.objects.filter(images__isnull=True).count()
    paginator      = Paginator(qs, per_page)
    page_obj       = paginator.get_page(request.GET.get('page'))
    parent_cats    = (Category.objects
                     .filter(is_active=True, parent=None)
                     .prefetch_related('subcategories')
                     .order_by('display_order', 'name'))

    return render(request, 'panel/products_list.html', {
        'page_obj':       page_obj,
        'q':              q,
        'cat_filter':     cat,
        'active_filter':  active,
        'no_image':       no_image,
        'parent_cats':    parent_cats,
        'total_count':    total_count,
        'no_image_count': no_image_count,
        'per_page':       per_page_param,
    })


@_staff
@require_POST
def product_bulk_action(request):
    action   = request.POST.get('action', '')
    scope    = request.POST.get('scope', 'selected')
    valid_actions = ('activate', 'deactivate', 'delete', 'set_price')

    if action not in valid_actions:
        return JsonResponse({'ok': False, 'error': 'Acción inválida'}, status=400)

    if scope == 'selected':
        raw_pks = request.POST.getlist('pks')
        if not raw_pks:
            return JsonResponse({'ok': False, 'error': 'No hay productos seleccionados'}, status=400)
        pks = [int(p) for p in raw_pks if p.isdigit()]
        qs  = Product.objects.filter(pk__in=pks)

    elif scope == 'all':
        qs = _apply_product_filters(
            Product.objects.all(),
            q        = request.POST.get('q', '').strip(),
            cat      = request.POST.get('cat', '').strip(),
            active   = request.POST.get('active', '').strip(),
            no_image = request.POST.get('no_image', '').strip(),
        )

    elif scope == 'no_image':
        qs = Product.objects.filter(images__isnull=True)

    else:
        return JsonResponse({'ok': False, 'error': 'Alcance inválido'}, status=400)

    if action == 'activate':
        count = qs.update(is_active=True)
    elif action == 'deactivate':
        count = qs.update(is_active=False)
    elif action == 'delete':
        count, _ = qs.delete()
    elif action == 'set_price':
        try:
            new_price = Decimal(request.POST.get('new_price', ''))
            if new_price < 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return JsonResponse({'ok': False, 'error': 'Precio inválido'}, status=400)
        count = qs.update(base_price=new_price)

    return JsonResponse({'ok': True, 'count': count, 'action': action})


@_staff
@require_POST
def product_toggle(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'ok': True, 'is_active': product.is_active})


def _save_product(data, product=None):
    """Valida y guarda un producto. Retorna (product, errors)."""
    errors = []
    sku               = data.get('sku', '').strip()
    name              = data.get('name', '').strip()
    base_price        = data.get('base_price', '').strip()
    category_id       = data.get('category', '').strip()
    status            = data.get('status', 'available')
    description       = data.get('description', '').strip()
    supplier_url      = data.get('supplier_url', '').strip()
    min_qty           = data.get('min_order_qty', '1').strip() or '1'
    ship_ov           = data.get('shipping_override', '').strip()
    price_ov          = data.get('price_override', '').strip()
    is_active         = data.get('is_active') == 'on'
    has_color_variants = data.get('has_color_variants') == 'on'
    size_group_raw    = data.get('size_group', '').strip()
    size_group_id     = int(size_group_raw) if size_group_raw else None

    if not name:         errors.append('El nombre es requerido.')
    if not base_price:   errors.append('El precio base es requerido.')
    if not category_id:  errors.append('La categoría es requerida.')

    if product is None:
        if not sku:      errors.append('El SKU es requerido.')
        elif Product.objects.filter(sku=sku).exists():
            errors.append('Ya existe un producto con ese SKU.')

    if errors:
        return None, errors

    try:
        kwargs = dict(
            name=name,
            description=description,
            category_id=int(category_id),
            base_price=float(base_price),
            status=status,
            supplier_url=supplier_url,
            min_order_qty=int(min_qty),
            shipping_override=float(ship_ov) if ship_ov else None,
            price_override=float(price_ov) if price_ov else None,
            is_active=is_active,
            has_color_variants=has_color_variants,
            size_group_id=size_group_id,
        )
        if product is None:
            kwargs['sku'] = sku
            product = Product.objects.create(**kwargs)
        else:
            for k, v in kwargs.items():
                setattr(product, k, v)
            product.save()
        return product, []
    except Exception as e:
        return None, [f'Error al guardar: {e}']


@_staff
def product_create(request):
    from types import SimpleNamespace
    errors = []
    if request.method == 'POST':
        obj, errors = _save_product(request.POST)
        if not errors:
            return redirect('panel:products_list')

    return render(request, 'panel/product_form.html', {
        'categories':     Category.objects.filter(is_active=True),
        'size_groups':    SizeGroup.objects.order_by('name'),
        'status_choices': Product.STATUS_CHOICES,
        'errors':         errors,
        'is_edit':        False,
        'product':        SimpleNamespace(
            name='', description='', supplier_url='',
            base_price='', shipping_override='', min_order_qty=1,
            is_active=True, status='available', category_id=None,
            price_override=None, has_color_variants=False, size_group_id=None,
        ),
        'data':           request.POST if request.method == 'POST' else {},
    })


@_staff
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    errors  = []
    if request.method == 'POST':
        _, errors = _save_product(request.POST, product=product)
        if not errors:
            return redirect('panel:products_list')

    return render(request, 'panel/product_form.html', {
        'product':        product,
        'categories':     Category.objects.filter(is_active=True),
        'size_groups':    SizeGroup.objects.order_by('name'),
        'status_choices': Product.STATUS_CHOICES,
        'errors':         errors,
        'is_edit':        True,
        'data':           request.POST if request.method == 'POST' else {},
    })


@_staff
@require_POST
def product_delete(request, pk):
    get_object_or_404(Product, pk=pk).delete()
    return redirect('panel:products_list')


# ─── Product images ───────────────────────────────────────────────────────────

@_staff
@require_POST
def product_image_upload(request, pk):
    product = get_object_or_404(Product, pk=pk)
    files = request.FILES.getlist('images')
    if not files:
        return JsonResponse({'ok': False, 'error': 'Sin archivos'}, status=400)
    for f in files:
        err = _validate_image_upload(f)
        if err:
            return JsonResponse({'ok': False, 'error': err}, status=400)
    has_cover = product.images.filter(is_cover=True).exists()
    created = []
    for i, f in enumerate(files):
        is_cover = not has_cover and i == 0
        img = ProductImage.objects.create(product=product, image=f, is_cover=is_cover)
        if is_cover:
            has_cover = True
        created.append({'pk': img.pk, 'url': img.image.url, 'is_cover': img.is_cover, 'color_label': img.color_label})
    return JsonResponse({'ok': True, 'images': created})


@_staff
@require_POST
def product_image_delete(request, img_pk):
    img = get_object_or_404(ProductImage, pk=img_pk)
    img.image.delete(save=False)
    img.delete()
    return JsonResponse({'ok': True})


@_staff
@require_POST
def product_image_set_cover(request, img_pk):
    img = get_object_or_404(ProductImage, pk=img_pk)
    img.is_cover = True
    img.save()
    return JsonResponse({'ok': True})


@_staff
@require_POST
def product_image_label(request, img_pk):
    """Guarda el nombre de color de una imagen (modo colorway)."""
    img = get_object_or_404(ProductImage, pk=img_pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)
    img.color_label = data.get('label', '').strip()[:60]
    img.save(update_fields=['color_label'])
    return JsonResponse({'ok': True, 'label': img.color_label})


# ─── Catalog config (category images + subcategory order) ────────────────────

@_staff
def catalog_config(request):
    top_cats = (Category.objects
                .filter(parent=None)
                .prefetch_related('subcategories__size_group')
                .order_by('-is_active', 'display_order', 'name'))
    cats_data = []
    for cat in top_cats:
        subs = list(cat.subcategories.all().select_related('size_group').order_by('-is_active', 'display_order', 'name'))
        cats_data.append({'cat': cat, 'subs': subs})
    size_groups = list(SizeGroup.objects.order_by('name'))
    return render(request, 'panel/catalog_config.html', {'cats_data': cats_data, 'size_groups': size_groups})


@_staff
@require_POST
def category_toggle_active(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    cat.is_active = not cat.is_active
    cat.save(update_fields=['is_active'])
    return JsonResponse({'ok': True, 'is_active': cat.is_active})


@_staff
@require_POST
def category_toggle_color_variants(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    cat.has_color_variants = not cat.has_color_variants
    cat.save(update_fields=['has_color_variants'])
    return JsonResponse({'ok': True, 'has_color_variants': cat.has_color_variants})


@_staff
def category_images_list(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    qs = ProductImage.objects.filter(
        Q(product__category=cat) | Q(product__category__parent=cat),
        product__is_active=True,
    ).select_related('product').order_by('-is_cover', 'display_order')[:60]
    return JsonResponse({
        'images': [{'pk': img.pk, 'url': img.image.url, 'product': img.product.name} for img in qs]
    })


@_staff
@require_POST
def category_image_from_product(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    img_pk = request.POST.get('image_pk', '')
    if not img_pk.isdigit():
        return JsonResponse({'ok': False, 'error': 'image_pk inválido'}, status=400)
    src = get_object_or_404(ProductImage, pk=int(img_pk))
    if cat.image:
        cat.image.delete(save=False)
    from django.core.files.base import ContentFile
    import os
    name = os.path.basename(src.image.name)
    cat.image.save(f'cat_{cat_pk}_{name}', ContentFile(src.image.read()), save=True)
    return JsonResponse({'ok': True, 'url': cat.image.url})


@_staff
@require_POST
def category_reorder(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    direction = request.POST.get('direction')
    if direction not in ('up', 'down'):
        return JsonResponse({'ok': False, 'error': 'Dirección inválida'}, status=400)

    siblings = list(
        Category.objects.filter(parent=cat.parent, is_active=True)
        .order_by('display_order', 'name')
    )
    idx = next((i for i, s in enumerate(siblings) if s.pk == cat.pk), None)
    if idx is None:
        return JsonResponse({'ok': False, 'error': 'No encontrado'}, status=404)

    if direction == 'up' and idx > 0:
        siblings[idx], siblings[idx - 1] = siblings[idx - 1], siblings[idx]
    elif direction == 'down' and idx < len(siblings) - 1:
        siblings[idx], siblings[idx + 1] = siblings[idx + 1], siblings[idx]

    for i, s in enumerate(siblings):
        s.display_order = i
    Category.objects.bulk_update(siblings, ['display_order'])
    return JsonResponse({'ok': True})


@_staff
@require_POST
def category_reorder_bulk(request, parent_pk):
    import json
    try:
        data  = json.loads(request.body)
        order = [int(pk) for pk in data.get('order', [])]
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)
    cats = {c.pk: c for c in Category.objects.filter(pk__in=order, parent_id=parent_pk)}
    to_update = []
    for i, pk in enumerate(order):
        if pk in cats:
            cats[pk].display_order = i
            to_update.append(cats[pk])
    if to_update:
        Category.objects.bulk_update(to_update, ['display_order'])
    return JsonResponse({'ok': True})


@_staff
def subcategory_products_reorder(request, subcat_pk):
    subcat = get_object_or_404(Category, pk=subcat_pk, parent__isnull=False)
    products = list(
        Product.objects.filter(category=subcat)
        .prefetch_related('images')
        .order_by('display_order', '-created_at')
    )
    return render(request, 'panel/subcategory_products_reorder.html', {
        'subcat': subcat,
        'products': products,
    })


@_staff
@require_POST
def subcategory_products_reorder_bulk(request, subcat_pk):
    subcat = get_object_or_404(Category, pk=subcat_pk)
    import json
    try:
        data = json.loads(request.body)
        order = [int(pk) for pk in data.get('order', [])]
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)
    
    products = {p.pk: p for p in Product.objects.filter(pk__in=order, category=subcat)}
    to_update = []
    for i, pk in enumerate(order):
        if pk in products:
            products[pk].display_order = i
            to_update.append(products[pk])
    if to_update:
        Product.objects.bulk_update(to_update, ['display_order'])
    return JsonResponse({'ok': True})


@_staff
@require_POST
def category_rename(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'El nombre no puede estar vacío'}, status=400)
    from django.utils.text import slugify
    base_slug = slugify(name)
    slug = base_slug
    # Resolver conflictos de slug con sufijo numérico
    n = 1
    while Category.objects.exclude(pk=cat_pk).filter(slug=slug).exists():
        slug = f'{base_slug}-{n}'
        n += 1
    cat.name = name
    cat.slug = slug
    try:
        cat.save(update_fields=['name', 'slug'])
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'Error al guardar: {e}'}, status=500)
    return JsonResponse({'ok': True, 'name': cat.name, 'slug': cat.slug})


def _save_category(post, files=None, cat=None, force_parent=_UNSET):
    from django.utils.text import slugify
    name = post.get('name', '').strip()
    if not name:
        return None, ['El nombre es obligatorio.']
    try:
        slug_input = post.get('slug', '').strip()
        obj = cat or Category()
        obj.name = name
        obj.slug = slug_input or slugify(name)
        if force_parent is _UNSET:
            parent_id = post.get('parent', '').strip()
            obj.parent_id = int(parent_id) if parent_id else None
        else:
            obj.parent_id = force_parent
        obj.shipping_cost = Decimal(post.get('shipping_cost', '0') or '0')
        obj.profit_margin = Decimal(post.get('profit_margin', '100') or '100')
        obj.min_order_qty    = int(post.get('min_order_qty', '1') or '1')
        obj.min_qty_per_item = int(post.get('min_qty_per_item', '0') or '0')
        obj.display_order    = int(post.get('display_order', '0') or '0')
        obj.is_active = 'is_active' in post
        obj.banner_text = post.get('banner_text', '').strip()
        if files and files.get('image'):
            err = _validate_image_upload(files['image'])
            if err:
                return None, [err]
            obj.image = files['image']
        obj.save()
        return obj, []
    except Exception as e:
        return None, [f'Error al guardar: {e}']


@_staff
def category_create(request):
    errors = []
    if request.method == 'POST':
        _, errors = _save_category(request.POST, request.FILES, force_parent=None)
        if not errors:
            return redirect('panel:catalog_config')
    return render(request, 'panel/category_form.html', {
        'errors':  errors,
        'is_edit': False,
        'cat':     None,
        'data':    request.POST if request.method == 'POST' else {},
    })


@_staff
@require_POST
def subcat_create(request, parent_pk):
    parent = get_object_or_404(Category, pk=parent_pk, parent__isnull=True)
    name = request.POST.get('name', '').strip()
    if name:
        from django.utils.text import slugify
        Category.objects.create(
            name=name,
            slug=slugify(name),
            parent=parent,
            is_active=True,
        )
    return redirect('panel:catalog_config')


@_staff
def category_edit(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    root_cats = (Category.objects.filter(parent__isnull=True)
                 .exclude(pk=cat_pk).order_by('display_order', 'name'))
    errors = []
    delete_error = request.GET.get('delete_error', '')
    if request.method == 'POST':
        _, errors = _save_category(request.POST, request.FILES, cat=cat)
        if not errors:
            return redirect('panel:catalog_config')
    return render(request, 'panel/category_form.html', {
        'cat':          cat,
        'root_cats':    root_cats,
        'errors':       errors,
        'delete_error': delete_error,
        'is_edit':      True,
        'data':         request.POST if request.method == 'POST' else {},
        'tiers':        cat.volume_tiers.all(),
    })


@_staff
@require_POST
def category_hard_delete(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    sub_count = cat.subcategories.count()
    if sub_count:
        return redirect(f'/panel/categorias/{cat_pk}/editar/?delete_error=Tiene+{sub_count}+subcategoría(s).+Elimínalas+primero.')
    prod_count = cat.products.count()
    if prod_count:
        return redirect(f'/panel/categorias/{cat_pk}/editar/?delete_error=Tiene+{prod_count}+producto(s)+asignado(s).+Desactívalos+o+cámbiales+la+categoría+primero.')
    cat.delete()
    return redirect('panel:catalog_config')


@_staff
@require_POST
def category_banner_save(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    cat.banner_text = request.POST.get('banner_text', '').strip()
    cat.save(update_fields=['banner_text'])
    return JsonResponse({'ok': True})


@_staff
@require_POST
def category_delete(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk, parent__isnull=False)
    count = cat.products.count()
    if count:
        return JsonResponse(
            {'ok': False, 'error': f'No se puede eliminar: tiene {count} producto{"s" if count != 1 else ""}. Desactívala o mueve los productos primero.'},
            status=400,
        )
    cat.delete()
    return JsonResponse({'ok': True})


# ─── Volume tiers ─────────────────────────────────────────────────────────────

@_staff
@require_POST
def tier_add(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    try:
        min_qty         = int(request.POST.get('min_qty', 0))
        discount_amount = Decimal(request.POST.get('unit_price', '0'))
        if min_qty < 1 or discount_amount <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)

    tier, created = VolumeTier.objects.get_or_create(
        category=cat, min_qty=min_qty,
        defaults={'discount_amount': discount_amount},
    )
    if not created:
        tier.discount_amount = discount_amount
        tier.save(update_fields=['discount_amount'])

    return JsonResponse({'ok': True, 'id': tier.pk, 'min_qty': tier.min_qty,
                         'unit_price': str(tier.discount_amount)})


@_staff
@require_POST
def tier_delete(request, tier_pk):
    tier = get_object_or_404(VolumeTier, pk=tier_pk)
    tier.delete()
    return JsonResponse({'ok': True})


# ─── Category images ─────────────────────────────────────────────────────────

@_staff
@require_POST
def category_image_upload(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    f = request.FILES.get('image')
    if not f:
        return JsonResponse({'ok': False, 'error': 'Sin archivo'}, status=400)
    err = _validate_image_upload(f)
    if err:
        return JsonResponse({'ok': False, 'error': err}, status=400)
    if cat.image:
        cat.image.delete(save=False)
    cat.image = f
    cat.save(update_fields=['image'])
    return JsonResponse({'ok': True, 'url': cat.image.url})


@_staff
@require_POST
def category_image_clear(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    if cat.image:
        cat.image.delete(save=False)
        cat.image = None
        cat.save(update_fields=['image'])
    return JsonResponse({'ok': True})


# ─── Sections ────────────────────────────────────────────────────────────────

@_staff
def sections_config(request):
    top_cats = (Category.objects
                .filter(parent=None)
                .order_by('display_order', 'name'))
    cat_data = []
    for cat in top_cats:
        subcats = list(cat.subcategories.filter(is_active=True).order_by('display_order', 'name'))
        if not subcats:
            continue
        sections_qs = cat.sections.prefetch_related('categories').order_by('display_order', 'name')
        all_secs = list(sections_qs)
        all_used_pks = set()
        for sec in all_secs:
            all_used_pks.update(sec.categories.values_list('pk', flat=True))
        sections_list = [
            {'sec': sec, 'available': [s for s in subcats if s.pk not in all_used_pks]}
            for sec in all_secs
        ]
        cat_data.append({'cat': cat, 'sections': sections_list, 'subcats': subcats})
    return render(request, 'panel/sections.html', {'cat_data': cat_data})


@_staff
@require_POST
def section_create(request):
    name = request.POST.get('name', '').strip()
    parent_id = request.POST.get('parent_id', '')
    if name and parent_id.isdigit():
        parent = get_object_or_404(Category, pk=int(parent_id), parent=None)
        order = Section.objects.filter(parent=parent).count()
        Section.objects.create(name=name, parent=parent, display_order=order)
    return redirect('panel:sections_config')


@_staff
@require_POST
def section_delete(request, pk):
    get_object_or_404(Section, pk=pk).delete()
    return redirect('panel:sections_config')


@_staff
@require_POST
def section_rename(request, pk):
    sec = get_object_or_404(Section, pk=pk)
    name = request.POST.get('name', '').strip()
    if name:
        sec.name = name
        sec.save(update_fields=['name'])
    return redirect('panel:sections_config')


@_staff
@require_POST
def section_reorder(request, pk):
    sec = get_object_or_404(Section, pk=pk)
    direction = request.POST.get('direction')
    if direction not in ('up', 'down'):
        return JsonResponse({'ok': False}, status=400)
    siblings = list(Section.objects.filter(parent=sec.parent).order_by('display_order', 'name'))
    idx = next((i for i, s in enumerate(siblings) if s.pk == sec.pk), None)
    if idx is None:
        return JsonResponse({'ok': False}, status=404)
    if direction == 'up' and idx > 0:
        siblings[idx], siblings[idx - 1] = siblings[idx - 1], siblings[idx]
    elif direction == 'down' and idx < len(siblings) - 1:
        siblings[idx], siblings[idx + 1] = siblings[idx + 1], siblings[idx]
    for i, s in enumerate(siblings):
        s.display_order = i
    Section.objects.bulk_update(siblings, ['display_order'])
    return redirect('panel:sections_config')


@_staff
@require_POST
def section_add_cat(request, pk):
    sec = get_object_or_404(Section, pk=pk)
    cat_id = request.POST.get('cat_id', '')
    if cat_id.isdigit():
        cat = get_object_or_404(Category, pk=int(cat_id), parent=sec.parent)
        sec.categories.add(cat)
    return redirect('panel:sections_config')


@_staff
@require_POST
def section_remove_cat(request, sec_pk, cat_pk):
    sec = get_object_or_404(Section, pk=sec_pk)
    sec.categories.remove(cat_pk)
    return redirect('panel:sections_config')


# ─── SubcategorySection (secciones dentro de subcategorías) ──────────────────

@_staff
def subsection_config(request, subcat_pk):
    subcat = get_object_or_404(Category, pk=subcat_pk, parent__isnull=False)
    sections_qs = (
        SubcategorySection.objects
        .filter(subcategory=subcat)
        .prefetch_related('products__images')
        .order_by('display_order', 'name')
    )
    all_secs = list(sections_qs)
    used_pks = set()
    for sec in all_secs:
        used_pks.update(sec.products.values_list('pk', flat=True))

    all_products = list(
        Product.objects
        .filter(category=subcat, is_active=True)
        .prefetch_related('images')
        .order_by('display_order', 'name')
    )
    sections_data = [
        {
            'sec': sec,
            'available': [p for p in all_products if p.pk not in used_pks],
        }
        for sec in all_secs
    ]
    return render(request, 'panel/subsection_config.html', {
        'subcat':        subcat,
        'sections_data': sections_data,
        'all_products':  all_products,
    })


@_staff
@require_POST
def subsection_create(request, subcat_pk):
    subcat = get_object_or_404(Category, pk=subcat_pk, parent__isnull=False)
    name = request.POST.get('name', '').strip()
    if name:
        order = SubcategorySection.objects.filter(subcategory=subcat).count()
        SubcategorySection.objects.create(name=name, subcategory=subcat, display_order=order)
    return redirect('panel:subsection_config', subcat_pk=subcat_pk)


@_staff
@require_POST
def subsection_delete(request, pk):
    sec = get_object_or_404(SubcategorySection, pk=pk)
    subcat_pk = sec.subcategory_id
    sec.delete()
    return redirect('panel:subsection_config', subcat_pk=subcat_pk)


@_staff
@require_POST
def subsection_rename(request, pk):
    sec = get_object_or_404(SubcategorySection, pk=pk)
    name = request.POST.get('name', '').strip()
    if name:
        sec.name = name
        sec.save(update_fields=['name'])
    return redirect('panel:subsection_config', subcat_pk=sec.subcategory_id)


@_staff
@require_POST
def subsection_reorder(request, pk):
    sec = get_object_or_404(SubcategorySection, pk=pk)
    direction = request.POST.get('direction')
    if direction not in ('up', 'down'):
        return JsonResponse({'ok': False}, status=400)
    siblings = list(
        SubcategorySection.objects
        .filter(subcategory=sec.subcategory)
        .order_by('display_order', 'name')
    )
    idx = next((i for i, s in enumerate(siblings) if s.pk == sec.pk), None)
    if idx is None:
        return JsonResponse({'ok': False}, status=404)
    if direction == 'up' and idx > 0:
        siblings[idx], siblings[idx - 1] = siblings[idx - 1], siblings[idx]
    elif direction == 'down' and idx < len(siblings) - 1:
        siblings[idx], siblings[idx + 1] = siblings[idx + 1], siblings[idx]
    for i, s in enumerate(siblings):
        s.display_order = i
    SubcategorySection.objects.bulk_update(siblings, ['display_order'])
    return redirect('panel:subsection_config', subcat_pk=sec.subcategory_id)


@_staff
@require_POST
def subsection_add_product(request, pk):
    sec = get_object_or_404(SubcategorySection, pk=pk)
    prod_id = request.POST.get('prod_id', '')
    if prod_id.isdigit():
        product = get_object_or_404(Product, pk=int(prod_id), category=sec.subcategory)
        sec.products.add(product)
    return redirect('panel:subsection_config', subcat_pk=sec.subcategory_id)


@_staff
@require_POST
def subsection_remove_product(request, sec_pk, prod_pk):
    sec = get_object_or_404(SubcategorySection, pk=sec_pk)
    sec.products.remove(prod_pk)
    return redirect('panel:subsection_config', subcat_pk=sec.subcategory_id)


# ─── Hero slides ─────────────────────────────────────────────────────────────

_VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.avi', '.mkv'}


def _is_video(f):
    import os
    ext = os.path.splitext(f.name)[1].lower()
    return ext in _VIDEO_EXTS or (getattr(f, 'content_type', '') or '').startswith('video/')


@_staff
@require_POST
def hero_slide_upload(request):
    files = request.FILES.getlist('files')
    if not files:
        return JsonResponse({'ok': False, 'error': 'Sin archivos'}, status=400)
    last_order = HeroSlide.objects.order_by('-display_order').values_list('display_order', flat=True).first() or 0
    created = []
    for i, f in enumerate(files):
        if _is_video(f):
            err = _validate_video_upload(f)
            if err:
                return JsonResponse({'ok': False, 'error': err}, status=400)
            order = last_order + i + 1
            slide = HeroSlide.objects.create(video=f, media_type=HeroSlide.MEDIA_VIDEO, display_order=order)
        else:
            err = _validate_image_upload(f)
            if err:
                return JsonResponse({'ok': False, 'error': err}, status=400)
            order = last_order + i + 1
            slide = HeroSlide.objects.create(image=f, media_type=HeroSlide.MEDIA_IMAGE, display_order=order)
        created.append({'pk': slide.pk, 'url': slide.file_url, 'type': slide.media_type, 'order': order})
    return JsonResponse({'ok': True, 'slides': created})


@_staff
@require_POST
def hero_slide_delete(request, pk):
    slide = get_object_or_404(HeroSlide, pk=pk)
    slide.delete_files()
    slide.delete()
    return JsonResponse({'ok': True})


@_staff
@require_POST
def hero_slide_reorder(request, pk):
    slide = get_object_or_404(HeroSlide, pk=pk)
    direction = request.POST.get('direction')
    if direction not in ('up', 'down'):
        return JsonResponse({'ok': False, 'error': 'Dirección inválida'}, status=400)
    siblings = list(HeroSlide.objects.order_by('display_order'))
    idx = next((i for i, s in enumerate(siblings) if s.pk == pk), None)
    if idx is None:
        return JsonResponse({'ok': False}, status=404)
    if direction == 'up' and idx > 0:
        siblings[idx], siblings[idx - 1] = siblings[idx - 1], siblings[idx]
    elif direction == 'down' and idx < len(siblings) - 1:
        siblings[idx], siblings[idx + 1] = siblings[idx + 1], siblings[idx]
    for i, s in enumerate(siblings):
        s.display_order = i
    HeroSlide.objects.bulk_update(siblings, ['display_order'])
    return JsonResponse({'ok': True})


@_staff
@require_POST
def hero_slide_toggle(request, pk):
    slide = get_object_or_404(HeroSlide, pk=pk)
    slide.is_active = not slide.is_active
    slide.save(update_fields=['is_active'])
    return JsonResponse({'ok': True, 'is_active': slide.is_active})


# ─── Grupos de tallas ────────────────────────────────────────────────────────

@_staff
def sizes_list(request):
    groups = SizeGroup.objects.annotate(cat_count=Count('categories')).order_by('name')
    subcats = list(
        Category.objects
        .filter(parent__isnull=False)
        .select_related('parent', 'size_group')
        .order_by('parent__name', 'name')
    )
    return render(request, 'panel/sizes.html', {'groups': groups, 'subcats': subcats})


@_staff
def size_group_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        sizes_raw = request.POST.get('sizes_json', '[]')
        conv_raw = request.POST.get('conversion_json', '').strip()
        try:
            _raw = [s.strip() for s in json.loads(sizes_raw) if str(s).strip()]
            sizes = list(dict.fromkeys(_raw))  # dedup preservando orden
        except (json.JSONDecodeError, ValueError):
            sizes = []
        if not name or not sizes:
            groups = SizeGroup.objects.annotate(cat_count=Count('categories')).order_by('name')
            return render(request, 'panel/sizes.html', {
                'groups': groups,
                'form_error': 'El nombre y al menos una talla son obligatorios.',
                'form_name': name,
                'form_sizes': sizes_raw,
                'form_conv': conv_raw,
                'show_form': True,
            })
        try:
            conv = json.loads(conv_raw) if conv_raw else None
        except (json.JSONDecodeError, ValueError):
            conv = None
        SizeGroup.objects.create(name=name, sizes=sizes, conversion_table=conv)
        return redirect('panel:sizes_list')
    return redirect('panel:sizes_list')


@_staff
def size_group_edit(request, pk):
    group = get_object_or_404(SizeGroup, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        sizes_raw = request.POST.get('sizes_json', '[]')
        conv_raw = request.POST.get('conversion_json', '').strip()
        try:
            _raw = [s.strip() for s in json.loads(sizes_raw) if str(s).strip()]
            sizes = list(dict.fromkeys(_raw))  # dedup preservando orden
        except (json.JSONDecodeError, ValueError):
            sizes = []
        if not name or not sizes:
            return render(request, 'panel/size_form.html', {
                'group': group,
                'form_error': 'El nombre y al menos una talla son obligatorios.',
            })
        try:
            conv = json.loads(conv_raw) if conv_raw else None
        except (json.JSONDecodeError, ValueError):
            conv = None
        group.name = name
        group.sizes = sizes
        group.conversion_table = conv
        group.save()
        return redirect('panel:sizes_list')
    return render(request, 'panel/size_form.html', {'group': group})


@_staff
@require_POST
def size_group_delete(request, pk):
    group = get_object_or_404(SizeGroup, pk=pk)
    if group.categories.exists():
        from django.contrib import messages
        messages.error(request, f'No se puede eliminar "{group.name}" — está asignado a subcategorías.')
        return redirect('panel:sizes_list')
    group.delete()
    return redirect('panel:sizes_list')


@_staff
@require_POST
def category_set_size_group(request, cat_pk):
    cat = get_object_or_404(Category, pk=cat_pk)
    try:
        body = json.loads(request.body)
        sg_id = body.get('size_group_id')
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'ok': False, 'error': 'Datos inválidos'}, status=400)
    if sg_id is None:
        cat.size_group = None
    else:
        cat.size_group = get_object_or_404(SizeGroup, pk=int(sg_id))
    cat.save(update_fields=['size_group'])
    name = cat.size_group.name if cat.size_group else ''
    return JsonResponse({'ok': True, 'size_group_name': name})


# ── Modaverse auto-order ──────────────────────────────────────────────────────

@_staff
@require_POST
def supplier_order_init(request, pk):
    order = get_object_or_404(Order, pk=pk)
    supplier_order, created = SupplierOrder.objects.get_or_create(order=order)
    if not created:
        return redirect('panel:supplier_order_detail', pk=pk)

    for oi in order.items.select_related('product').all():
        url = ''
        status = 'pending'
        if oi.product and oi.product.supplier_url and 'modaverse' in oi.product.supplier_url:
            url = oi.product.supplier_url
        else:
            status = 'no_url'
        SupplierOrderItem.objects.create(
            supplier_order=supplier_order,
            order_item=oi,
            supplier_url=url,
            variant_target=oi.variant_snapshot or '',
            status=status,
        )
    return redirect('panel:supplier_order_detail', pk=pk)


@_staff
def supplier_order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)
    supplier_order = get_object_or_404(
        SupplierOrder.objects.prefetch_related(
            'items__order_item__product__category__parent',
            'items__order_item__product__images',
        ),
        order=order,
    )
    return render(request, 'panel/supplier_order.html', {
        'order': order,
        'supplier_order': supplier_order,
    })


@_staff
@require_POST
def supplier_item_update(request, pk, item_pk):
    """Marca manualmente un SupplierOrderItem como agregado o pendiente y recalcula el estado del pedido proveedor."""
    order = get_object_or_404(Order, pk=pk)
    supplier_order = get_object_or_404(SupplierOrder, order=order)
    item = get_object_or_404(SupplierOrderItem, pk=item_pk, supplier_order=supplier_order)

    new_status = request.POST.get('status', '')
    if new_status not in ('added', 'pending', 'variant_not_found', 'no_url'):
        return JsonResponse({'ok': False, 'error': 'Estado inválido'})

    item.status = new_status
    if new_status == 'added' and not item.notes:
        item.notes = 'Agregado manualmente'
    elif new_status == 'pending' and item.notes == 'Agregado manualmente':
        item.notes = ''
    item.save(update_fields=['status', 'notes'])

    all_statuses = set(supplier_order.items.values_list('status', flat=True))
    if all_statuses <= {'added', 'no_url'}:
        supplier_order.status = 'done'
    elif 'added' in all_statuses:
        supplier_order.status = 'partial'
    elif 'variant_not_found' in all_statuses:
        supplier_order.status = 'failed'
    else:
        supplier_order.status = 'pending'
    supplier_order.save(update_fields=['status', 'updated_at'])

    return JsonResponse({
        'ok':                  True,
        'item_status':         item.status,
        'item_status_display': item.get_status_display(),
        'item_notes':          item.notes,
        'order_status':        supplier_order.status,
        'order_status_display': supplier_order.get_status_display(),
    })


@_staff
@require_POST
def supplier_order_run(request, pk):
    """Lanza sync_modaverse_order --headless como subproceso en background."""
    order = get_object_or_404(Order, pk=pk)
    supplier_order = get_object_or_404(SupplierOrder, order=order)

    if supplier_order.status == 'running':
        return JsonResponse({'ok': False, 'error': 'Ya está en progreso'})

    # Resetear ítems fallidos/no encontrados para reintentar
    supplier_order.items.filter(status='variant_not_found').update(status='pending', notes='')
    supplier_order.status = 'pending'
    supplier_order.save(update_fields=['status'])

    manage_py = Path(__file__).resolve().parent.parent / 'manage.py'
    env = {**os.environ, 'PYTHONUTF8': '1'}
    log_path = Path(tempfile.gettempdir()) / f'modaverse_{order.pk}.log'
    log_f = open(log_path, 'w', encoding='utf-8', errors='replace')
    subprocess.Popen(
        [sys.executable, str(manage_py), 'sync_modaverse_order', str(order.pk), '--headless'],
        cwd=str(manage_py.parent),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    log_f.close()

    return JsonResponse({'ok': True, 'log': str(log_path)})


@_staff
def supplier_order_status(request, pk):
    """Devuelve el estado actual del SupplierOrder para polling AJAX."""
    order = get_object_or_404(Order, pk=pk)
    supplier_order = get_object_or_404(
        SupplierOrder.objects.prefetch_related('items__order_item'),
        order=order,
    )

    items = [
        {
            'sku':            si.order_item.sku_snapshot,
            'status':         si.status,
            'status_display': si.get_status_display(),
            'notes':          si.notes,
        }
        for si in supplier_order.items.all()
    ]

    return JsonResponse({
        'status':         supplier_order.status,
        'status_display': supplier_order.get_status_display(),
        'cart_script':    supplier_order.cart_script,
        'items':          items,
    })


# ── Productos pendientes de aprobación ────────────────────────────────────────

_PENDING_PER_PAGE = {'24': 24, '48': 48, '96': 96}

@_staff
def pendientes_list(request):
    status_filter = request.GET.get('status', 'pending')
    if status_filter not in ('pending', 'approved', 'rejected'):
        status_filter = 'pending'

    q   = request.GET.get('q', '').strip()
    cat = request.GET.get('cat', '').strip()

    qs = (PendingProduct.objects
          .select_related('category', 'category__parent')
          .order_by('-created_at'))
    if status_filter != 'all':
        qs = qs.filter(status=status_filter)
    if q:
        qs = qs.filter(
            Q(display_name__icontains=q) | Q(modaverse_name__icontains=q)
        )
    if cat:
        qs = qs.filter(
            Q(category__slug=cat) | Q(category__parent__slug=cat)
        )

    counts = {
        'pending':  PendingProduct.objects.filter(status='pending').count(),
        'approved': PendingProduct.objects.filter(status='approved').count(),
        'rejected': PendingProduct.objects.filter(status='rejected').count(),
    }

    per_page_param = request.GET.get('per_page', '24')
    if per_page_param not in _PENDING_PER_PAGE and per_page_param != 'todos':
        per_page_param = '24'
    per_page = qs.count() if per_page_param == 'todos' else _PENDING_PER_PAGE[per_page_param]

    paginator = Paginator(qs, per_page or 24)
    page = paginator.get_page(request.GET.get('page'))

    root_cats = (Category.objects
                 .filter(parent=None, is_active=True)
                 .prefetch_related('subcategories')
                 .order_by('name'))

    _fparams = {}
    if q: _fparams['q'] = q
    if cat: _fparams['cat'] = cat
    if per_page_param != '24': _fparams['per_page'] = per_page_param
    filter_qs = _urlencode(_fparams)

    return render(request, 'panel/pendientes.html', {
        'page_obj':      page,
        'status_filter': status_filter,
        'counts':        counts,
        'root_cats':     root_cats,
        'parent_cats':   root_cats,
        'q':             q,
        'cat_filter':    cat,
        'per_page':      per_page_param,
        'filter_qs':     filter_qs,
    })


@_staff
@require_POST
def pendiente_approve(request, pk):
    pending = get_object_or_404(PendingProduct, pk=pk, status='pending')
    name       = request.POST.get('display_name', '').strip()
    price_raw  = request.POST.get('base_price', '').strip()
    cat_pk     = request.POST.get('category', '')

    if name:
        pending.display_name = name
    if price_raw:
        try:
            pending.base_price = Decimal(price_raw)
        except InvalidOperation:
            pass
    if cat_pk:
        try:
            pending.category = Category.objects.get(pk=cat_pk)
        except Category.DoesNotExist:
            pass

    pending.save(update_fields=['display_name', 'base_price', 'category'])
    try:
        pending.approve()
    except Exception as e:
        from django.contrib import messages
        messages.error(request, f'Error al aprobar {pending.display_name}: {e}')

    params = {'status': 'pending'}
    if q := request.POST.get('_q', ''): params['q'] = q
    if c := request.POST.get('_cat', ''): params['cat'] = c
    pp = request.POST.get('_per_page', '24')
    if pp != '24': params['per_page'] = pp
    if pg := request.POST.get('_page', ''): params['page'] = pg
    return redirect(f'/panel/pendientes/?{_urlencode(params)}')


@_staff
@require_POST
def pendiente_reject(request, pk):
    pending = get_object_or_404(PendingProduct, pk=pk, status='pending')
    pending.reject(notes=request.POST.get('notes', ''))

    params = {'status': 'pending'}
    if q := request.POST.get('_q', ''): params['q'] = q
    if c := request.POST.get('_cat', ''): params['cat'] = c
    pp = request.POST.get('_per_page', '24')
    if pp != '24': params['per_page'] = pp
    if pg := request.POST.get('_page', ''): params['page'] = pg
    return redirect(f'/panel/pendientes/?{_urlencode(params)}')


@_staff
@require_POST
def pendientes_approve_all(request):
    """Aprueba o rechaza en lote los PendingProducts cuyos PKs se envían."""
    action = request.POST.get('action', 'approve')
    if action not in ('approve', 'reject'):
        action = 'approve'
    pks = [int(v) for v in request.POST.getlist('pks') if v.isdigit()]
    count, errors = 0, []
    for pending in PendingProduct.objects.filter(pk__in=pks, status='pending'):
        try:
            if action == 'approve':
                pending.approve()
            else:
                pending.reject()
            count += 1
        except Exception as e:
            errors.append(f'{pending.display_name}: {e}')
    return JsonResponse({'approved': count, 'errors': errors})
