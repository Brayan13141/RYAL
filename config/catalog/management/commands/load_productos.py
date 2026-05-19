"""
Carga productos desde scraped_modaverse.json y scraped_yupoo.json.
Crea la jerarquía de categorías (padre → subcategorías) desde el árbol del JSON.
Ejecutar: python manage.py load_productos
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse, quote, urlunparse

import httpx

from django.core.files.base import ContentFile
from django.utils.text import slugify
from django.core.management.base import BaseCommand

from catalog.models import Category, Product, ProductImage, Tag

# Pricing por categoría padre (slug → {shipping, margin, order})
PARENT_PRICING = {
    'gorra':                               {'shipping': 0,   'margin': 100, 'order': 1},
    'camisetas-deportivas-y-jerseys-de-futbol': {'shipping': 0, 'margin': 100, 'order': 2},
    'camisetas-sudaderas-calidad-g5':      {'shipping': 0,   'margin': 100, 'order': 3},
    'camisetas-sudaderas-calidad-11':      {'shipping': 0,   'margin': 100, 'order': 4},
    'electronica':                         {'shipping': 0,   'margin': 100, 'order': 5},
    'calzado':                             {'shipping': 280, 'margin': 100, 'order': 6},
}
_DEFAULT_PRICING = {'shipping': 0, 'margin': 100, 'order': 99}

# Prefijo de SKU por categoría padre
PARENT_PREFIX = {
    'gorra':    'CAP',
    'calzado':  'TN2',
    'electronica': 'ELC',
}
_DEFAULT_PREFIX = 'GEN'


BASE_URL = 'https://putianshoefactory.x.yupoo.com'

_DL_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
}


def _clean_url(url: str) -> str:
    """Limpia control chars y percent-encodea paths con caracteres no-ASCII."""
    url = re.sub(r'[\r\n\t]', '', url).strip()
    if not url:
        return url
    p = urlparse(url)
    encoded_path = quote(p.path, safe='/:@!$&\'()*+,;=')
    return urlunparse(p._replace(path=encoded_path))


def _get_ext(url: str) -> str:
    path = urlparse(url).path
    filename = path.rsplit('/', 1)[-1]
    if '.' in filename:
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext.isalpha() and 2 <= len(ext) <= 5:
            return ext
    return 'jpg'


def _download_image(url: str, timeout: int = 20, referer: str | None = None):
    url = _clean_url(url)
    if not url:
        return None
    parsed = urlparse(url)
    auto_referer = f"{parsed.scheme}://{parsed.netloc}/"
    headers = {**_DL_HEADERS, 'Referer': referer or auto_referer}
    try:
        r = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


def _next_sku_index(prefix: str) -> int:
    existing = Product.objects.filter(
        sku__startswith=f'RYL-{prefix}-'
    ).values_list('sku', flat=True)
    nums = []
    for sku in existing:
        try:
            nums.append(int(sku.rsplit('-', 1)[-1]))
        except (ValueError, IndexError):
            pass
    return (max(nums) + 1) if nums else 1


def _clean_name(raw) -> str:
    if not raw:
        return ''
    raw = str(raw)
    name = re.sub(r'\s*[-–]\s*\$?\d+\s*(pesos?|mxn|usd|cny)?', '', raw, flags=re.IGNORECASE)
    name = name.strip(' .-_')
    name = re.sub(r'\s+[一-鿿㐀-䶿]+\s*$', '', name).strip()
    return name or raw


class Command(BaseCommand):
    help = 'Carga productos desde scraped_modaverse.json y scraped_yupoo.json'

    def add_arguments(self, parser):
        parser.add_argument('--no-images', action='store_true',
                            help='Omitir descarga de imágenes')
        parser.add_argument('--only', choices=['modaverse', 'calzado', 'all'],
                            default='modaverse')
        parser.add_argument('--recategorize', action='store_true',
                            help='Re-categoriza productos actualmente en categoría General usando el JSON')
        parser.add_argument('--fix-urls', action='store_true',
                            help='Actualiza supplier_url de productos modaverse ya importados al nuevo formato (categoryId)')

    def handle(self, *args, **options):
        no_images = options['no_images']
        only      = options['only']

        tag_nuevo, _ = Tag.objects.get_or_create(
            name='Nuevo', defaults={'color_hex': '#C9A84C'}
        )
        self.stdout.write('✓ Tag "Nuevo" listo')

        if options.get('fix_urls'):
            self._fix_modaverse_urls()
            return

        if only in ('modaverse', 'all'):
            self._load_modaverse(tag_nuevo, no_images)

        if only in ('calzado', 'all'):
            self._load_calzado(tag_nuevo, no_images)

        if options.get('recategorize'):
            self._recategorize_general()

        self.stdout.write(self.style.SUCCESS('\n✅ Carga completada. Revisa el admin.'))

    # ── Modaverse (multi-categoría con jerarquía) ──────────────────────────────

    def _load_modaverse(self, tag_nuevo, no_images):
        self.stdout.write('\n── Cargando desde scraped_modaverse.json ──')
        json_path = Path(__file__).resolve().parents[4] / 'scraped_modaverse.json'

        if not json_path.exists():
            self.stdout.write(self.style.WARNING(f'  ⚠ No se encontró {json_path}'))
            return

        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        products = data.get('products', [])
        categories_tree = data.get('categories', [])

        if not products:
            self.stdout.write(self.style.WARNING('  ⚠ Sin productos en JSON'))
            return

        self.stdout.write(f'  {len(products)} productos · {len(categories_tree)} categorías padre en JSON')

        # ── 1. Crear jerarquía de categorías ──────────────────────────────────
        # Mapa: category_id_api → Django Category object
        cat_map = {}  # api_id → Category

        if categories_tree:
            self.stdout.write('\n  Creando jerarquía de categorías...')
            for order_idx, cat_data in enumerate(categories_tree):
                parent_name = cat_data.get('name_es') or cat_data.get('name_zh') or 'General'
                parent_slug = slugify(parent_name)[:50]
                pricing     = PARENT_PRICING.get(parent_slug, _DEFAULT_PRICING)

                parent_obj, created = Category.objects.get_or_create(
                    slug=parent_slug,
                    defaults={
                        'name':          parent_name,
                        'parent':        None,
                        'shipping_cost': pricing['shipping'],
                        'profit_margin': pricing['margin'],
                        'display_order': pricing['order'],
                        'is_active':     True,
                    }
                )
                cat_map[cat_data['id']] = parent_obj
                action = 'creada' if created else 'ya existe'
                self.stdout.write(f'    [PADRE] {parent_name} — {action}')

                for sub_data in cat_data.get('subcategories', []):
                    sub_name = sub_data.get('name_es') or sub_data.get('name_zh') or 'Sub'
                    # Asegurar slug único combinando padre + sub si hace falta
                    sub_slug_base = slugify(sub_name)[:45]
                    sub_slug = sub_slug_base
                    # Si el slug ya existe y pertenece a otro padre, añadir sufijo
                    existing = Category.objects.filter(slug=sub_slug).exclude(parent=parent_obj).first()
                    if existing:
                        sub_slug = f'{sub_slug_base}-{parent_slug[:8]}'[:50]

                    sub_obj, sub_created = Category.objects.get_or_create(
                        slug=sub_slug,
                        defaults={
                            'name':          sub_name,
                            'parent':        parent_obj,
                            'shipping_cost': pricing['shipping'],
                            'profit_margin': pricing['margin'],
                            'display_order': 0,
                            'is_active':     True,
                        }
                    )
                    # Si ya existe pero no tiene parent, asignarlo
                    if not sub_created and sub_obj.parent is None:
                        sub_obj.parent = parent_obj
                        sub_obj.save(update_fields=['parent'])

                    cat_map[sub_data['id']] = sub_obj

            self.stdout.write(f'  → {len(cat_map)} categorías/subcategorías mapeadas')
        else:
            # Sin árbol en JSON: fallback — categoría gorras plana
            self.stdout.write('  ⚠ Sin árbol de categorías en JSON — usando categoría plana')
            gorras, _ = Category.objects.get_or_create(
                slug='gorras',
                defaults={'name': 'Gorras', 'shipping_cost': 0, 'profit_margin': 100,
                          'display_order': 1, 'is_active': True}
            )
            cat_map['__default__'] = gorras

        # ── 2. Cargar productos ────────────────────────────────────────────────
        self.stdout.write('\n  Cargando productos...')

        # Agrupar por categoría padre para SKU consecutivo
        prefix_counters = {}

        for p in products:
            api_cat_id = p.get('category_id', '') or '__default__'
            cat_obj    = cat_map.get(api_cat_id)

            if cat_obj is None:
                cat_name = p.get('category', '') or 'General'
                cat_slug = slugify(cat_name)[:50]

                # Primero buscar en BD como subcategoría existente
                cat_obj = Category.objects.filter(slug=cat_slug).exclude(parent=None).first()

                # Si no, buscar como cualquier categoría
                if cat_obj is None:
                    cat_obj = Category.objects.filter(slug=cat_slug).first()

                # Si sigue sin encontrarse, crear como subcategoría de "General" (no como raíz huérfana)
                if cat_obj is None:
                    # Buscar o crear categoría padre "General"
                    general_parent, _ = Category.objects.get_or_create(
                        slug='general',
                        defaults={
                            'name': 'General',
                            'shipping_cost': 0,
                            'profit_margin': 100,
                            'display_order': 99,
                            'is_active': True,
                        }
                    )
                    cat_obj, _ = Category.objects.get_or_create(
                        slug=cat_slug,
                        defaults={
                            'name': cat_name,
                            'parent': general_parent,
                            'shipping_cost': 0,
                            'profit_margin': 100,
                            'display_order': 0,
                            'is_active': True,
                        }
                    )
                    # Si ya existía como huérfana (sin parent), asignarle el parent General
                    if cat_obj.parent is None and cat_obj.slug != 'general':
                        cat_obj.parent = general_parent
                        cat_obj.save(update_fields=['parent'])

            # Determinar prefijo de SKU desde la categoría padre
            root = cat_obj.parent or cat_obj
            prefix = PARENT_PREFIX.get(slugify(root.name), _DEFAULT_PREFIX)

            if prefix not in prefix_counters:
                prefix_counters[prefix] = _next_sku_index(prefix)

            sku   = f"RYL-{prefix}-{prefix_counters[prefix]:03d}"
            name  = _clean_name(p.get('name') or f'Producto {prefix_counters[prefix]}')
            base  = float(p.get('price_mxn') or p.get('price_usd') or 0) or 200.0

            created = self._create_product(
                sku=sku, name=name, category=cat_obj,
                base_price=base,
                description=p.get('description', ''),
                supplier_url=p.get('url', ''),
                images=p.get('images', []),
                tag=tag_nuevo,
                no_images=no_images,
            )
            if created:
                prefix_counters[prefix] += 1

    # ── Calzado (yupoo_pf — por marcas) ───────────────────────────────────────

    def _load_calzado(self, tag_nuevo, no_images):
        self.stdout.write('\n── Cargando calzado (yupoo_pf) ──')

        json_path = Path(__file__).resolve().parents[4] / 'scraped_yupoo_pf.json'
        if not json_path.exists():
            self.stdout.write(self.style.WARNING(f'  ⚠ No se encontró {json_path}'))
            self.stdout.write('  Ejecuta primero: python scrape_yupoo_pf.py')
            return

        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        products       = data.get('products', [])
        categories_tree = data.get('categories', [])

        if not products:
            self.stdout.write(self.style.WARNING('  ⚠ Sin productos en scraped_yupoo_pf.json'))
            return

        self.stdout.write(f'  {len(products)} productos en JSON')

        # ── Crear jerarquía Calzado → Marca ───────────────────────────────────
        cat_map: dict[str, object] = {}

        for cat_data in categories_tree:
            parent_name = cat_data.get('name_es') or 'Calzado'
            parent_slug = slugify(parent_name)[:50]
            pricing     = PARENT_PRICING.get(parent_slug, _DEFAULT_PRICING)

            parent_obj, created = Category.objects.get_or_create(
                slug=parent_slug,
                defaults={
                    'name':          parent_name,
                    'parent':        None,
                    'shipping_cost': pricing['shipping'],
                    'profit_margin': pricing['margin'],
                    'display_order': pricing['order'],
                    'is_active':     True,
                },
            )
            cat_map[cat_data['id']] = parent_obj
            self.stdout.write(f"    [PADRE] {parent_name} — {'creada' if created else 'ya existe'}")

            for sub in cat_data.get('subcategories', []):
                sub_name = sub.get('name_es') or sub.get('name_zh') or 'Sub'
                sub_slug = slugify(sub_name)[:50]
                existing = Category.objects.filter(slug=sub_slug).exclude(parent=parent_obj).first()
                if existing:
                    sub_slug = f'{sub_slug}-{parent_slug[:6]}'[:50]

                sub_obj, sub_created = Category.objects.get_or_create(
                    slug=sub_slug,
                    defaults={
                        'name':          sub_name,
                        'parent':        parent_obj,
                        'shipping_cost': pricing['shipping'],
                        'profit_margin': pricing['margin'],
                        'display_order': 0,
                        'is_active':     True,
                    },
                )
                if not sub_created and sub_obj.parent is None:
                    sub_obj.parent = parent_obj
                    sub_obj.save(update_fields=['parent'])

                cat_map[sub['id']] = sub_obj

        # ── Cargar productos ──────────────────────────────────────────────────
        self.stdout.write('\n  Cargando productos...')
        prefix_counters: dict[str, int] = {}

        for p in products:
            sub_id  = p.get('category_id', '')
            cat_obj = cat_map.get(sub_id)

            if cat_obj is None:
                brand_name = p.get('category', 'Calzado')
                brand_slug = slugify(brand_name)[:50]
                cat_obj    = Category.objects.filter(slug=brand_slug).first()
                if cat_obj is None:
                    # Buscar o crear padre Calzado
                    calzado, _ = Category.objects.get_or_create(
                        slug='calzado',
                        defaults={
                            'name': 'Calzado', 'shipping_cost': 280, 'profit_margin': 100,
                            'display_order': 7, 'is_active': True,
                        },
                    )
                    cat_obj, _ = Category.objects.get_or_create(
                        slug=brand_slug,
                        defaults={
                            'name': brand_name, 'parent': calzado,
                            'shipping_cost': 280, 'profit_margin': 100,
                            'display_order': 0, 'is_active': True,
                        },
                    )

            root   = cat_obj.parent or cat_obj
            prefix = PARENT_PREFIX.get(slugify(root.name), 'TN2')

            if prefix not in prefix_counters:
                prefix_counters[prefix] = _next_sku_index(prefix)

            sku   = f"RYL-{prefix}-{prefix_counters[prefix]:03d}"
            name  = _clean_name(p.get('name') or f'Producto {prefix_counters[prefix]}')
            base  = float(p.get('price_mxn') or 500.0)

            created = self._create_product(
                sku=sku, name=name, category=cat_obj,
                base_price=base,
                description=p.get('description', 'Tenis de importación directa.'),
                supplier_url=p.get('url', ''),
                images=p.get('images', []),
                tag=tag_nuevo,
                no_images=no_images,
                img_referer=BASE_URL,
            )
            if created:
                prefix_counters[prefix] += 1

    # ── Re-categorizar productos en "General" ──────────────────────────────────

    def _recategorize_general(self):
        self.stdout.write('\n── Re-categorizando productos en "General" ──')
        json_path = Path(__file__).resolve().parents[4] / 'scraped_modaverse.json'
        if not json_path.exists():
            self.stdout.write(self.style.WARNING('  ⚠ No se encontró scraped_modaverse.json'))
            return

        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        # Construir mapa supplier_url → category info (soporta formato viejo y nuevo)
        url_to_cat = {}
        pid_to_cat = {}  # fallback: productId numérico → category info
        for p in data.get('products', []):
            info = {'cat_id': p.get('category_id', ''), 'cat_name': p.get('category', '')}
            url_to_cat[p['url']] = info
            pid = p.get('sku', '')  # el JSON guarda productId en 'sku'
            if pid:
                pid_to_cat[pid] = info

        # Reconstruir cat_map desde el árbol del JSON
        cat_map = {}
        for cat in data.get('categories', []):
            slug = slugify(cat.get('name_es') or cat.get('name_zh') or '')[:50]
            parent_obj = Category.objects.filter(slug=slug).first()
            if parent_obj:
                cat_map[cat['id']] = parent_obj
            for sub in cat.get('subcategories', []):
                sub_slug = slugify(sub.get('name_es') or sub.get('name_zh') or '')[:50]
                sub_obj = Category.objects.filter(slug=sub_slug).first()
                if sub_obj:
                    cat_map[sub['id']] = sub_obj

        general_cat = Category.objects.filter(slug='general').first()
        if not general_cat:
            self.stdout.write('  No hay categoría "General" en BD')
            return

        productos_general = Product.objects.filter(category=general_cat)
        total = productos_general.count()
        self.stdout.write(f'  Productos en General: {total}')

        import re as _re
        _old_pid_re = _re.compile(r'#/product/(\d{15,})')

        moved = not_found = 0
        for product in productos_general:
            info = url_to_cat.get(product.supplier_url)
            if not info:
                # Fallback: extraer pid del URL (ambos formatos)
                m = _old_pid_re.search(product.supplier_url or '')
                if m:
                    info = pid_to_cat.get(m.group(1))
            if not info:
                not_found += 1
                continue

            cat_id = info['cat_id']
            cat_name = info['cat_name']

            new_cat = cat_map.get(cat_id)
            if new_cat is None:
                cat_slug = slugify(cat_name)[:50]
                new_cat = Category.objects.filter(slug=cat_slug).exclude(slug='general').first()

            if new_cat and new_cat != general_cat:
                product.category = new_cat
                product.save(update_fields=['category'])
                moved += 1
            else:
                not_found += 1

        self.stdout.write(
            self.style.SUCCESS(f'  Movidos: {moved}, sin categoría disponible: {not_found}')
        )

    # ── Actualizar supplier_url de productos modaverse al nuevo formato ────────

    def _fix_modaverse_urls(self):
        self.stdout.write('\n── Actualizando supplier_url de productos modaverse ──')
        json_path = Path(__file__).resolve().parents[4] / 'scraped_modaverse.json'
        if not json_path.exists():
            self.stdout.write(self.style.WARNING(f'  ⚠ No se encontró {json_path}'))
            return

        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        # Mapa: pid → cat_id (del JSON regenerado con el nuevo formato)
        pid_to_cat = {}
        for p in data.get('products', []):
            pid = p.get('sku', '')    # el JSON guarda productId en sku
            cat = p.get('category_id', '')
            if pid and cat:
                pid_to_cat[pid] = cat

        self.stdout.write(f'  {len(pid_to_cat)} productos en JSON')

        # Los productos ya en DB con URLs antiguas tienen el pid al final:
        # https://www.modaverse.vip/#/product/{numericPid}
        import re as _re
        old_pattern = _re.compile(r'#/product/(\d{15,})$')

        updated = skipped = 0
        for product in Product.objects.filter(
            supplier_url__contains='modaverse.vip/#/product/'
        ).only('pk', 'supplier_url'):
            m = old_pattern.search(product.supplier_url)
            if not m:
                skipped += 1
                continue
            pid = m.group(1)
            cat_id = pid_to_cat.get(pid, '')
            if cat_id:
                new_url = f"https://www.modaverse.vip/#/product/{cat_id}?pid={pid}"
            else:
                new_url = product.supplier_url  # sin cambio si no hay cat_id
            if new_url != product.supplier_url:
                product.supplier_url = new_url
                product.save(update_fields=['supplier_url'])
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Actualizados: {updated}  sin cambio: {skipped}'
        ))

    # ── Helper genérico ────────────────────────────────────────────────────────

    def _create_product(self, sku, name, category, base_price, description,
                        supplier_url, images, tag, no_images, img_referer=None):
        if supplier_url and Product.objects.filter(supplier_url=supplier_url).exists():
            return False
        if Product.objects.filter(sku=sku).exists():
            self.stdout.write(f'    ↩ {sku} ya existe')
            return False

        product = Product.objects.create(
            sku=sku, name=name, category=category, base_price=base_price,
            description=description, supplier_url=supplier_url,
            status='available', is_active=True,
        )
        product.tags.add(tag)

        if not no_images and images:
            for order, img_url in enumerate(images[:150]):
                if not img_url:
                    continue
                img_bytes = _download_image(img_url, referer=img_referer)
                if img_bytes:
                    ext = _get_ext(img_url)
                    pi = ProductImage(
                        product=product,
                        is_cover=(order == 0),
                        display_order=order,
                    )
                    pi.image.save(f"{sku}_{order}.{ext}", ContentFile(img_bytes), save=True)

        self.stdout.write(
            f'    ✓ {sku} — {name[:40]} → {category.name} (${base_price:.0f} → ${product.final_price:.0f} MXN)'
        )
        return True
