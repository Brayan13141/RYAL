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

from catalog.models import Category, Product, ProductImage, Tag, SizeGroup
from catalog.modaverse import pid_from_url, category_filter_ids, read_modaverse_json


def _build_existing_pids(supplier_urls) -> set:
    """Set de productIds presentes en BD, extraídos de los supplier_url.
    Deduplica por pid (no por string de URL) para reconciliar los formatos
    #/proinfo/{pid} y #/product/{cat}?pid={pid} del mismo producto."""
    pids = set()
    for url in supplier_urls:
        pid = pid_from_url(url)
        if pid:
            pids.add(pid)
    return pids


def get_or_create_size_group(sizes) -> 'SizeGroup':
    """Find-or-create de un SizeGroup para un conjunto de tallas de ropa.

    Dedup por el CONTENIDO del conjunto (insensible al orden), pero conserva el
    orden de aparición en `sizes` para el display (S·M·L·XL, no alfabético). Así,
    la misma talla-set en distinto orden no crea grupos duplicados.
    conversion_table = None (la ropa no usa tabla EU/MX/US como el calzado)."""
    canonical = list(dict.fromkeys(sizes))          # display order preservado
    target = set(canonical)
    for sg in SizeGroup.objects.filter(name__startswith='Ropa · '):
        if set(sg.sizes) == target:
            return sg
    name = ('Ropa · ' + '·'.join(canonical))[:100]
    return SizeGroup.objects.create(name=name, sizes=canonical, conversion_table=None)


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

# Precio base por defecto (MXN) cuando la API no devuelve precio, por slug de cat padre
_CATEGORY_DEFAULT_PRICE = {
    'gorra':                               150.0,
    'camisetas-deportivas-y-jerseys-de-futbol': 220.0,
    'camisetas-sudaderas-calidad-g5':      250.0,
    'camisetas-sudaderas-calidad-11':      350.0,
    'electronica':                         180.0,
    'calzado':                             500.0,
}
_DEFAULT_PRICE = 200.0

# Nombres que indican entradas informativas, no productos reales
_SKIP_NAMES = {'0', '', 'tabla de medidas', 'size chart', 'talla', 'medidas'}


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


# Alias backward-compat — movido a catalog/modaverse.py
_category_filter_ids = category_filter_ids


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
        parser.add_argument('--category', nargs='+', metavar='KEYWORD',
                            help='Cargar solo categorías modaverse que coincidan '
                                 '(parcial, sin distinción de mayúsculas). Ej: --category "van cleef"')
        parser.add_argument('--recategorize', action='store_true',
                            help='Re-categoriza productos actualmente en categoría General usando el JSON')
        parser.add_argument('--fix-urls', '--fix-proinfo-urls', action='store_true',
                            help='Actualiza supplier_url de productos modaverse al formato directo #/proinfo/{pid}')

    def handle(self, *args, **options):
        no_images = options['no_images']
        only      = options['only']
        category  = options.get('category')

        tag_nuevo, _ = Tag.objects.get_or_create(
            name='Nuevo', defaults={'color_hex': '#C9A84C'}
        )
        self.stdout.write('✓ Tag "Nuevo" listo')

        if options.get('fix_urls'):
            self._fix_modaverse_urls()
            return

        # Pre-cargar todos los supplier_url existentes en un set — 1 query compartida
        # evita hacer N queries individuales en los loops de carga
        existing_urls = set(
            Product.objects.exclude(supplier_url='')
            .exclude(supplier_url__isnull=True)
            .values_list('supplier_url', flat=True)
        )
        self.stdout.write(f'✓ {len(existing_urls)} productos ya en BD (skip automático)')

        if only in ('modaverse', 'all'):
            self._load_modaverse(tag_nuevo, no_images, existing_urls, category)

        if only in ('calzado', 'all'):
            self._load_calzado(tag_nuevo, no_images, existing_urls)

        if options.get('recategorize'):
            self._recategorize_general()

        self.stdout.write(self.style.SUCCESS('\n✅ Carga completada. Revisa el admin.'))

    # ── Modaverse (multi-categoría con jerarquía) ──────────────────────────────

    def _read_modaverse_json(self):
        """Lee scraped_modaverse.json. Wrapper con warning de stdout."""
        data = read_modaverse_json()
        if data is None:
            self.stdout.write(self.style.WARNING('  ⚠ No se encontró scraped_modaverse.json'))
        return data

    def _load_modaverse(self, tag_nuevo, no_images, existing_urls, category=None):
        self.stdout.write('\n── Cargando desde scraped_modaverse.json ──')

        data = self._read_modaverse_json()
        if data is None:
            return

        products = data.get('products', [])
        categories_tree = data.get('categories', [])

        if not products:
            self.stdout.write(self.style.WARNING('  ⚠ Sin productos en JSON'))
            return

        self.stdout.write(f'  {len(products)} productos · {len(categories_tree)} categorías padre en JSON')

        # ── Filtro opcional por categoría (--category) ────────────────────────
        filter_ids = None
        if category:
            filter_ids = _category_filter_ids(categories_tree, category)
            if not filter_ids:
                self.stdout.write(self.style.WARNING(
                    f'  ⚠ Ninguna categoría coincide con: {category} — nada que cargar'
                ))
                return
            self.stdout.write(f'  Filtrando a {len(filter_ids)} categorías/subcategorías por {category}')

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
        new_count = skip_count = reclassified_count = 0

        # Dedup por productId (estable entre formatos #/proinfo y #/product?pid=)
        existing_pids = _build_existing_pids(existing_urls)

        # Productos YA en BD dentro del scope de --category, indexados por pid.
        # Permite enriquecer (size_group + variant_colors) en re-runs sin re-crear.
        existing_in_scope = {}
        if filter_ids is not None:
            django_cats = [cat_map[i] for i in filter_ids if i in cat_map]
            for prod in Product.objects.filter(
                category__in=django_cats
            ).only('pk', 'supplier_url', 'size_group_id', 'variant_colors'):
                ppid = pid_from_url(prod.supplier_url)
                if ppid:
                    existing_in_scope[ppid] = prod

        for p in products:
            # Filtro por categoría (--category): omitir productos fuera del set
            if filter_ids is not None and p.get('category_id', '') not in filter_ids:
                continue
            # Skip por productId — robusto al formato de supplier_url en BD
            pid = p.get('sku', '')
            if pid:
                if pid in existing_pids:
                    skip_count += 1
                    prod = existing_in_scope.get(pid)
                    if prod is not None:
                        self._apply_variants(
                            prod, p.get('sizes', []), p.get('colors', [])
                        )
                        # Reclasificar si la categoría cambió dentro del mismo scope
                        if filter_ids is not None:
                            new_cat = cat_map.get(p.get('category_id', ''))
                            if new_cat is not None and prod.category_id != new_cat.pk:
                                prod.category = new_cat
                                prod.save(update_fields=['category'])
                                reclassified_count += 1
                    continue
            # Sin pid (caso raro): caer al match por URL literal
            elif p.get('url', '') and p['url'] in existing_urls:
                skip_count += 1
                continue
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

            name = _clean_name(p.get('name') or '')
            # Filtrar entradas informativas que no son productos reales
            if not name or name.strip().lower() in _SKIP_NAMES:
                continue

            # Precio: usar el de la API; si es 0/None, usar default por categoría padre
            root_slug = slugify((cat_obj.parent or cat_obj).name)
            api_price = float(p.get('price_mxn') or p.get('price_usd') or 0)
            base = api_price if api_price > 0 else _CATEGORY_DEFAULT_PRICE.get(root_slug, _DEFAULT_PRICE)

            sku = f"RYL-{prefix}-{prefix_counters[prefix]:03d}"

            product_id = p.get('sku', '')  # JSON guarda productId en 'sku'
            supplier_url = (
                f"https://www.modaverse.vip/#/proinfo/{product_id}"
                if product_id else p.get('url', '')
            )
            created = self._create_product(
                sku=sku, name=name, category=cat_obj,
                base_price=base,
                description=p.get('description', ''),
                supplier_url=supplier_url,
                images=p.get('images', []),
                tag=tag_nuevo,
                no_images=no_images,
                sizes=p.get('sizes', []),
                colors=p.get('colors', []),
            )
            if created:
                existing_urls.add(supplier_url)
                if pid:
                    existing_pids.add(pid)
                prefix_counters[prefix] += 1
                new_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'  → {new_count} nuevos · {skip_count} ya existían (omitidos)'
                + (f' · {reclassified_count} recategorizados' if reclassified_count else '')
            )
        )

    # ── Calzado (yupoo_pf — por marcas) ───────────────────────────────────────

    def _load_calzado(self, tag_nuevo, no_images, existing_urls):
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
        new_count = skip_count = 0

        for p in products:
            if p.get('url', '') and p['url'] in existing_urls:
                skip_count += 1
                continue
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

            name = _clean_name(p.get('name') or '')
            if not name or name.strip().lower() in _SKIP_NAMES:
                continue

            api_price = float(p.get('price_mxn') or 0)
            base = api_price if api_price > 0 else 500.0

            sku = f"RYL-{prefix}-{prefix_counters[prefix]:03d}"

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
                existing_urls.add(p.get('url', ''))
                prefix_counters[prefix] += 1
                new_count += 1

        self.stdout.write(
            self.style.SUCCESS(f'  → {new_count} nuevos · {skip_count} ya existían (omitidos)')
        )

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

        # Extrae pid de cualquier formato de supplier_url modaverse:
        # #/proinfo/{pid}  |  #/product/{catId}?pid={pid}  |  #/product/{numericPid}
        import re as _re
        _pid_re = _re.compile(r'#/proinfo/(\d{15,})|[?&]pid=(\d{15,})|#/product/(\d{15,})$')

        moved = not_found = 0
        for product in productos_general:
            info = url_to_cat.get(product.supplier_url)
            if not info:
                m = _pid_re.search(product.supplier_url or '')
                if m:
                    pid = m.group(1) or m.group(2) or m.group(3)
                    info = pid_to_cat.get(pid)
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

    # ── Actualizar supplier_url de productos modaverse al formato #/proinfo/{pid} ─

    def _fix_modaverse_urls(self):
        self.stdout.write('\n── Actualizando supplier_url de modaverse → #/proinfo/{pid} ──')

        # Formato 1: #/product/{numericPid}  (sin categoryId, sku numérico)
        _pat_numeric = re.compile(r'#/product/(\d{15,})(?:[?#]|$)')
        # Formato 2: #/product/{catId}?pid={numericPid}
        _pat_pid_param = re.compile(r'[?&]pid=(\d{15,})')
        # Formato 3: #/product/PR{...}  (sku con prefijo PR — el más común)
        _pat_pr = re.compile(r'#/product/(PR\w+)')

        to_update = []
        already = no_match = 0

        for product in Product.objects.filter(
            supplier_url__contains='modaverse.vip'
        ).only('pk', 'supplier_url'):
            url = product.supplier_url or ''

            if '#/proinfo/' in url:
                already += 1
                continue

            m = _pat_numeric.search(url) or _pat_pid_param.search(url) or _pat_pr.search(url)
            if not m:
                no_match += 1
                continue

            pid = m.group(1)
            new_url = f"https://www.modaverse.vip/#/proinfo/{pid}"
            if new_url != url:
                product.supplier_url = new_url
                to_update.append(product)

        if to_update:
            Product.objects.bulk_update(to_update, ['supplier_url'], batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Actualizados: {len(to_update)}  ya en proinfo: {already}  sin match: {no_match}'
        ))

    # ── Helper genérico ────────────────────────────────────────────────────────

    def _apply_variants(self, product, sizes, colors):
        """Asigna size_group (find-or-create) y variant_colors al producto.
        Idempotente: solo guarda los campos que cambian."""
        changed = []
        if sizes:
            sg = get_or_create_size_group(sizes)
            if product.size_group_id != sg.pk:
                product.size_group = sg
                changed.append('size_group')
        if colors and product.variant_colors != list(colors):
            product.variant_colors = list(colors)
            changed.append('variant_colors')
        if changed:
            product.save(update_fields=changed)

    def _create_product(self, sku, name, category, base_price, description,
                        supplier_url, images, tag, no_images, img_referer=None,
                        sizes=None, colors=None):
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

        if sizes or colors:
            self._apply_variants(product, sizes or [], colors or [])

        if not no_images and images:
            for order, img_url in enumerate(images[:250]):
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
