"""
Asigna categoría a PendingProducts que la tienen en NULL.
Usa scraped_modaverse.json para recuperar la categoría original de cada producto.

Uso:
    python manage.py assign_pending_categories
    python manage.py assign_pending_categories --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from catalog.models import Category, PendingProduct
from catalog.modaverse import pid_from_url, read_modaverse_json

PARENT_PRICING = {
    'gorra':                               {'shipping': 0,   'margin': 100, 'order': 1},
    'camisetas-deportivas-y-jerseys-de-futbol': {'shipping': 0, 'margin': 100, 'order': 2},
    'camisetas-sudaderas-calidad-g5':      {'shipping': 0,   'margin': 100, 'order': 3},
    'camisetas-sudaderas-calidad-11':      {'shipping': 0,   'margin': 100, 'order': 4},
    'electronica':                         {'shipping': 0,   'margin': 100, 'order': 5},
    'calzado':                             {'shipping': 280, 'margin': 100, 'order': 6},
    'bolsos-de-lujo-de-gama-alta':         {'shipping': 0,   'margin': 200, 'order': 7},
}
_DEFAULT_PRICING = {'shipping': 0, 'margin': 100, 'order': 99}


class Command(BaseCommand):
    help = 'Asigna categoría a PendingProducts con category=NULL usando scraped_modaverse.json'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Muestra qué haría sin escribir en BD')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        orphans = list(
            PendingProduct.objects.filter(status='pending', category__isnull=True)
            .only('id', 'supplier_url', 'display_name', 'raw_data')
        )
        if not orphans:
            self.stdout.write(self.style.SUCCESS('✅ No hay pendientes sin categoría.'))
            return

        self.stdout.write(f'  {len(orphans)} pendientes sin categoría')

        data = read_modaverse_json()
        if data is None:
            self.stdout.write(self.style.ERROR('✗ No se encontró scraped_modaverse.json'))
            return

        products       = data.get('products', [])
        categories_tree = data.get('categories', [])

        # pid → category_id (API)
        pid_to_api_cat: dict[str, str] = {}
        for p in products:
            pid = p.get('sku', '')
            cat_id = p.get('category_id', '')
            if pid and cat_id:
                pid_to_api_cat[pid] = cat_id

        # api_cat_id → Django Category (get_or_create igual que load_productos)
        cat_map: dict[str, Category] = {}
        for cat_data in categories_tree:
            parent_name = cat_data.get('name_es') or cat_data.get('name_zh') or 'General'
            parent_slug = slugify(parent_name)[:50]
            pricing     = PARENT_PRICING.get(parent_slug, _DEFAULT_PRICING)

            parent_obj, _ = Category.objects.get_or_create(
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

            for sub_data in cat_data.get('subcategories', []):
                sub_name = sub_data.get('name_es') or sub_data.get('name_zh') or 'Sub'
                sub_slug_base = slugify(sub_name)[:45]
                sub_slug = sub_slug_base
                existing = Category.objects.filter(slug=sub_slug).exclude(parent=parent_obj).first()
                if existing:
                    sub_slug = f'{sub_slug_base}-{parent_slug[:8]}'[:50]

                sub_obj, _ = Category.objects.get_or_create(
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
                cat_map[sub_data['id']] = sub_obj

        self.stdout.write(f'  {len(cat_map)} categorías mapeadas desde JSON')

        # Fallback categories por prefijo SKU
        gorra_cat   = Category.objects.filter(slug='gorra').first()
        general_cat, _ = Category.objects.get_or_create(
            slug='general',
            defaults={'name': 'General', 'shipping_cost': 0, 'profit_margin': 100,
                      'display_order': 99, 'is_active': True},
        )
        _PREFIX_FALLBACK = {
            'CAP': gorra_cat,
            'TN2': Category.objects.filter(slug='calzado').first(),
            'ELC': Category.objects.filter(slug='electronica').first(),
        }

        updated = fallback = no_pid = 0

        for pending in orphans:
            pid = pid_from_url(pending.supplier_url)
            if not pid:
                no_pid += 1
                continue

            api_cat_id = pid_to_api_cat.get(pid, '')
            cat_obj    = cat_map.get(api_cat_id)

            if cat_obj is None:
                # Producto desapareció del JSON — fallback por prefijo RYL SKU
                ryl_sku = (pending.raw_data or {}).get('sku', '')
                prefix  = ryl_sku[4:7] if ryl_sku.startswith('RYL-') else ''
                cat_obj = _PREFIX_FALLBACK.get(prefix) or general_cat
                fallback += 1
                label = f'fallback→{cat_obj.name}'
            else:
                label = cat_obj.name
                updated += 1

            if dry_run:
                self.stdout.write(f'  [DRY] {pending.display_name[:40]:<40} → {label}')
            else:
                pending.category = cat_obj
                pending.save(update_fields=['category'])

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'\n[DRY-RUN] {updated} por JSON · {fallback} por fallback · {no_pid} sin pid (omitidos)'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\n✅ {updated} categorizados por JSON · {fallback} por fallback · {no_pid} sin pid (omitidos)'
            ))
