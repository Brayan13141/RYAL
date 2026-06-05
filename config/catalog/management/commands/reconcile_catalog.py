"""Management command: reconcile_catalog

Reconcilia el catálogo local con scraped_modaverse.json:
- Soft-delete de productos modaverse que ya no existen en el proveedor.
- Reactivación de los que reaparecen.

Solo afecta supplier_url que contenga 'modaverse.vip'.
Calzado (yupoo) y productos manuales quedan intactos.
"""
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from catalog.models import Category, Product
from catalog.modaverse import pid_from_url, read_modaverse_json, category_filter_ids


def _build_cat_map(categories_tree):
    """Mapea JSON category_id → Django Category (solo get, no crea).
    Refleja la misma lógica de slugs que load_productos para garantizar que
    las categorías ya existentes en BD son encontradas."""
    cat_map = {}
    for cat_data in categories_tree:
        parent_name = cat_data.get('name_es') or cat_data.get('name_zh') or ''
        if not parent_name:
            continue
        parent_slug = slugify(parent_name)[:50]
        parent_obj = Category.objects.filter(slug=parent_slug).first()
        if not parent_obj:
            continue
        cat_map[cat_data['id']] = parent_obj
        for sub_data in cat_data.get('subcategories', []):
            sub_name = sub_data.get('name_es') or sub_data.get('name_zh') or ''
            if not sub_name:
                continue
            sub_slug = slugify(sub_name)[:45]
            # Intenta slug exacto con parent; luego slug con sufijo (mirror de load_productos)
            sub_obj = (
                Category.objects.filter(slug=sub_slug, parent=parent_obj).first()
                or Category.objects.filter(
                    slug=f'{sub_slug}-{slugify(parent_name)[:8]}'[:50]
                ).first()
            )
            if sub_obj:
                cat_map[sub_data['id']] = sub_obj
    return cat_map


class Command(BaseCommand):
    help = (
        'Reconcilia el catálogo local con scraped_modaverse.json: '
        'soft-delete de removidos, reactivación de reaparecidos.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--category', nargs='+', metavar='KEYWORD',
            help='Limitar a categorías que coincidan (parcial, case-insensitive).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra el plan sin escribir nada.',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Salta guardas de zero-guard y umbral.',
        )
        parser.add_argument(
            '--max-deactivate-pct', type=int, default=30, metavar='N',
            dest='max_deactivate_pct',
            help='Porcentaje máximo de bajas antes de abortar (default 30).',
        )

    def handle(self, *args, **options):
        data = read_modaverse_json()
        if data is None:
            self.stderr.write(self.style.ERROR('No se encontró scraped_modaverse.json'))
            return

        products = data.get('products', [])
        categories_tree = data.get('categories', [])

        # ── Filtro por categoría ──────────────────────────────────────────────
        filter_ids = None
        if options['category']:
            filter_ids = category_filter_ids(categories_tree, options['category'])
            if not filter_ids:
                self.stderr.write(self.style.WARNING(
                    f'Ninguna categoría coincide con: {options["category"]}'
                ))
                return

        # ── Set vivo: todos los pids del JSON ENTERO ──────────────────────────
        live_pids = {p['sku'] for p in products if p.get('sku')}

        # ── JSON scope (para zero-guard) ──────────────────────────────────────
        if filter_ids is not None:
            json_scope = [p for p in products if p.get('category_id') in filter_ids]
        else:
            json_scope = products

        # ── Guarda 1: zero-guard ─────────────────────────────────────────────
        if not json_scope and not options['force']:
            self.stderr.write(self.style.ERROR(
                'Zero-guard: JSON scope vacío (0 productos). '
                'Posible scrape fallido. Usa --force para ignorar.'
            ))
            return

        # ── DB scope ─────────────────────────────────────────────────────────
        scope_qs = Product.objects.filter(supplier_url__icontains='modaverse.vip')
        if filter_ids is not None:
            cat_map = _build_cat_map(categories_tree)
            django_cat_pks = {cat_map[i].pk for i in filter_ids if i in cat_map}
            scope_qs = scope_qs.filter(category_id__in=django_cat_pks)

        # ── Candidatos ───────────────────────────────────────────────────────
        to_deactivate_pks = []
        scope_active_count = 0
        for p in scope_qs.filter(is_active=True):
            scope_active_count += 1
            pid = pid_from_url(p.supplier_url)
            if pid and pid not in live_pids:
                to_deactivate_pks.append(p.pk)

        to_reactivate_pks = []
        for p in scope_qs.filter(auto_deactivated=True):
            pid = pid_from_url(p.supplier_url)
            if pid and pid in live_pids:
                to_reactivate_pks.append(p.pk)

        # ── Guarda 2: umbral ─────────────────────────────────────────────────
        if to_deactivate_pks and scope_active_count > 0 and not options['force']:
            pct = len(to_deactivate_pks) / scope_active_count * 100
            if pct > options['max_deactivate_pct']:
                self.stderr.write(self.style.ERROR(
                    f'Umbral superado: {len(to_deactivate_pks)}/{scope_active_count} = '
                    f'{pct:.1f}% > {options["max_deactivate_pct"]}%. '
                    f'Usa --force para proceder.'
                ))
                return

        # ── Dry-run ───────────────────────────────────────────────────────────
        if options['dry_run']:
            self.stdout.write(
                f'[dry-run] scope={scope_active_count} · '
                f'bajas={len(to_deactivate_pks)} · '
                f'reactivaciones={len(to_reactivate_pks)}'
            )
            if to_deactivate_pks:
                examples = list(
                    Product.objects.filter(pk__in=to_deactivate_pks[:10])
                    .values_list('sku', 'name')
                )
                self.stdout.write(f'  A desactivar (primeros {len(examples)}):')
                for sku, name in examples:
                    self.stdout.write(f'    {sku} — {name}')
            return

        # ── Aplicar ───────────────────────────────────────────────────────────
        Product.objects.filter(pk__in=to_deactivate_pks).update(
            is_active=False, auto_deactivated=True
        )
        Product.objects.filter(pk__in=to_reactivate_pks).update(
            is_active=True, auto_deactivated=False
        )

        self.stdout.write(self.style.SUCCESS(
            f'scope={scope_active_count} · '
            f'bajas={len(to_deactivate_pks)} · '
            f'reactivaciones={len(to_reactivate_pks)}'
        ))
