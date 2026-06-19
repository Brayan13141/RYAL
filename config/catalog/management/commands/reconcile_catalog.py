"""Management command: reconcile_catalog

Reconcilia el catálogo local con scraped_modaverse.json:
- Soft-delete de productos modaverse que ya no existen en el proveedor.
- Reactivación de los que reaparecen.

Solo afecta supplier_url que contenga 'modaverse.vip'.
Calzado (yupoo) y productos manuales quedan intactos.
"""
from django.core.management.base import BaseCommand

from catalog.models import Category, Product, ProductImage
from catalog.modaverse import pid_from_url, read_modaverse_json, category_filter_ids


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
        parser.add_argument(
            '--prune', action='store_true',
            help='Eliminar permanentemente los productos auto-desactivados en el scope. '
                 'Usar después de reconcile_catalog (soft-delete ya aplicado). '
                 'Compatible con --dry-run y --category.',
        )

    def handle(self, *args, **options):
        # ── Rama --prune (independiente del flujo normal) ─────────────────────
        if options['prune']:
            self._prune(options)
            return

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

        # ── JSON scope ────────────────────────────────────────────────────────
        # Con --category: solo productos que están en las categorías actuales del JSON.
        # Sin --category: todos los productos del JSON.
        # live_pids se limita al scope para detectar productos que salieron
        # de una sección aunque sigan en otras categorías del JSON.
        if filter_ids is not None:
            json_scope = [p for p in products if p.get('category_id') in filter_ids]
        else:
            json_scope = products

        # Solo contar como "live" los productos publicados en Modaverse (yn_launch='1').
        # Si yn_launch no está en el JSON (datos anteriores), se asume '1' para
        # compatibilidad hacia atrás (no desactiva productos de scrapes viejos).
        live_pids = {
            p['sku'] for p in json_scope
            if p.get('sku') and p.get('yn_launch', '1') == '1'
        }

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
            kws = [k.lower() for k in options['category']]
            root_pks = {
                c.pk
                for c in Category.objects.filter(parent__isnull=True)
                if any(kw in c.name.lower() or kw in c.slug.lower() for kw in kws)
            }
            sub_pks = set(
                Category.objects.filter(parent_id__in=root_pks).values_list('pk', flat=True)
            )
            scope_qs = scope_qs.filter(category_id__in=root_pks | sub_pks)

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

        # Borrar imágenes de los productos dados de baja
        imgs_deleted = 0
        if to_deactivate_pks:
            imgs = list(ProductImage.objects.filter(product_id__in=to_deactivate_pks))
            for img in imgs:
                img.image.delete(save=False)
            ProductImage.objects.filter(product_id__in=to_deactivate_pks).delete()
            imgs_deleted = len(imgs)

        self.stdout.write(self.style.SUCCESS(
            f'scope={scope_active_count} · '
            f'bajas={len(to_deactivate_pks)} · '
            f'reactivaciones={len(to_reactivate_pks)} · '
            f'imágenes_eliminadas={imgs_deleted}'
        ))

    def _prune(self, options):
        """Elimina permanentemente los productos con auto_deactivated=True en el scope."""
        qs = Product.objects.filter(
            supplier_url__icontains='modaverse.vip',
            auto_deactivated=True,
        )
        if options['category']:
            kws = [k.lower() for k in options['category']]
            root_pks = {
                c.pk
                for c in Category.objects.filter(parent__isnull=True)
                if any(kw in c.name.lower() or kw in c.slug.lower() for kw in kws)
            }
            sub_pks = set(
                Category.objects.filter(parent_id__in=root_pks).values_list('pk', flat=True)
            )
            qs = qs.filter(category_id__in=root_pks | sub_pks)

        count = qs.count()
        if count == 0:
            self.stdout.write('Nada que limpiar (0 productos auto-desactivados en scope).')
            return

        if options['dry_run']:
            examples = list(qs.values_list('sku', 'name')[:10])
            self.stdout.write(f'[dry-run] prune={count} productos a eliminar permanentemente')
            for sku, name in examples:
                self.stdout.write(f'  {sku} — {name}')
            if count > 10:
                self.stdout.write(f'  … y {count - 10} más')
            return

        # Borrar archivos de imagen antes del hard-delete (CASCADE borra registros DB pero no archivos)
        pks = list(qs.values_list('pk', flat=True))
        imgs = list(ProductImage.objects.filter(product_id__in=pks))
        for img in imgs:
            img.image.delete(save=False)

        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Eliminados {count} productos + {len(imgs)} imágenes permanentemente.'
        ))
