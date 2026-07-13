"""Management command: reconcile_yupoo

Reconcilia el catálogo de Calzado (Yupoo) con scraped_yupoo_pf.json:
- Soft-delete de productos cuyo supplier_url ya no aparece en un scrape fresco.
- Reactivación de los que reaparecen.

Solo afecta supplier_url que contenga 'yupoo'. Modaverse y productos manuales
quedan intactos (ver reconcile_catalog.py para esos).

IMPORTANTE: a diferencia de modaverse (que trae yn_launch), Yupoo no expone un
flag de "publicado" — la única señal de que un modelo se descontinuó es que ya
no aparezca en un scrape COMPLETO (sin --brands ni --resume) de
scrape_yupoo_pf.json. Correr esto contra un JSON viejo/parcial puede desactivar
productos que siguen vivos. Verificar la fecha de scraped_yupoo_pf.json antes
de usar sin --dry-run.

Uso:
    python manage.py reconcile_yupoo --dry-run
    python manage.py reconcile_yupoo
    python manage.py reconcile_yupoo --force
    python manage.py reconcile_yupoo --prune
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from catalog.models import Product, ProductImage

_JSON_NAME = 'scraped_yupoo_pf.json'


def _json_path() -> Path:
    return Path(__file__).resolve().parents[4] / _JSON_NAME


def read_yupoo_json():
    path = _json_path()
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class Command(BaseCommand):
    help = (
        'Reconcilia el catálogo de Calzado (Yupoo) con scraped_yupoo_pf.json: '
        'soft-delete de modelos descontinuados, reactivación de reaparecidos.'
    )

    def add_arguments(self, parser):
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
            help='Eliminar permanentemente los productos auto-desactivados de Yupoo. '
                 'Usar después de reconcile_yupoo (soft-delete ya aplicado). '
                 'Compatible con --dry-run.',
        )

    def handle(self, *args, **options):
        if options['prune']:
            self._prune(options)
            return

        data = read_yupoo_json()
        if data is None:
            self.stderr.write(self.style.ERROR(f'No se encontró {_JSON_NAME}'))
            return

        products = data.get('products', [])
        live_urls = {p['url'] for p in products if p.get('url')}

        # ── Guarda 1: zero-guard ─────────────────────────────────────────────
        if not products and not options['force']:
            self.stderr.write(self.style.ERROR(
                'Zero-guard: JSON sin productos (0). Posible scrape fallido. '
                'Usa --force para ignorar.'
            ))
            return

        scope_qs = Product.objects.filter(supplier_url__icontains='yupoo')

        to_deactivate_pks = []
        scope_active_count = 0
        for p in scope_qs.filter(is_active=True):
            scope_active_count += 1
            if p.supplier_url not in live_urls:
                to_deactivate_pks.append(p.pk)

        to_reactivate_pks = []
        for p in scope_qs.filter(auto_deactivated=True):
            if p.supplier_url in live_urls:
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

        Product.objects.filter(pk__in=to_deactivate_pks).update(
            is_active=False, auto_deactivated=True
        )
        Product.objects.filter(pk__in=to_reactivate_pks).update(
            is_active=True, auto_deactivated=False
        )

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
        qs = Product.objects.filter(supplier_url__icontains='yupoo', auto_deactivated=True)
        count = qs.count()
        if count == 0:
            self.stdout.write('Nada que limpiar (0 productos auto-desactivados de Yupoo).')
            return

        if options['dry_run']:
            examples = list(qs.values_list('sku', 'name')[:10])
            self.stdout.write(f'[dry-run] prune={count} productos a eliminar permanentemente')
            for sku, name in examples:
                self.stdout.write(f'  {sku} — {name}')
            if count > 10:
                self.stdout.write(f'  … y {count - 10} más')
            return

        pks = list(qs.values_list('pk', flat=True))
        imgs = list(ProductImage.objects.filter(product_id__in=pks))
        for img in imgs:
            img.image.delete(save=False)

        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Eliminados {count} productos + {len(imgs)} imágenes permanentemente.'
        ))
