"""
Actualiza base_price de productos TN2 usando los precios del JSON scraped_yupoo_pf.json.
Hace match por supplier_url.

Uso:
    python manage.py update_calzado_prices
    python manage.py update_calzado_prices --dry-run
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from catalog.models import Product


class Command(BaseCommand):
    help = 'Actualiza base_price de productos TN2 desde scraped_yupoo_pf.json'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Muestra cambios sin guardar')

    def handle(self, *args, **options):
        dry = options['dry_run']

        json_path = Path(__file__).resolve().parents[4] / 'scraped_yupoo_pf.json'
        if not json_path.exists():
            self.stdout.write(self.style.ERROR(f'No se encontró {json_path}'))
            return

        self.stdout.write('Cargando JSON...')
        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        url_to_price = {
            p['url']: float(p['price_mxn'])
            for p in data.get('products', [])
            if p.get('url') and p.get('price_mxn')
        }
        self.stdout.write(f'  {len(url_to_price)} productos con precio en JSON')

        qs = Product.objects.filter(sku__startswith='RYL-TN2-').only(
            'pk', 'sku', 'name', 'base_price', 'supplier_url'
        )
        total = qs.count()
        self.stdout.write(f'  {total} productos TN2 en BD\n')

        updated = skipped = no_match = 0

        for product in qs:
            new_price = url_to_price.get(product.supplier_url)
            if new_price is None:
                no_match += 1
                continue

            if abs(new_price - float(product.base_price)) < 0.01:
                skipped += 1
                continue

            self.stdout.write(
                f'  {product.sku} {product.name[:35]:35s} '
                f'{product.base_price:.0f} → {new_price:.0f}'
            )
            if not dry:
                product.base_price = new_price
                product.save(update_fields=['base_price'])
            updated += 1

        suffix = ' (simulado)' if dry else ''
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ {updated} actualizados{suffix} · {skipped} sin cambio · {no_match} sin match en JSON'
        ))
