"""
Re-descarga imágenes corruptas (< min_size bytes) desde scraped_modaverse.json.
Sobrescribe el archivo en disco sin tocar el registro en BD.

Uso:
    python manage.py fix_corrupted_images
    python manage.py fix_corrupted_images --dry-run
    python manage.py fix_corrupted_images --min-size 1000
    python manage.py fix_corrupted_images --workers 5
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.models import ProductImage

HEADERS = {
    'Referer': 'https://www.modaverse.vip/',
    'Origin':  'https://www.modaverse.vip',
    'Accept':  'image/webp,image/apng,image/*,*/*;q=0.8',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}


def _download(url: str, min_size: int, timeout: int = 20):
    try:
        r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=timeout)
        if r.status_code == 200 and len(r.content) > min_size:
            return r.content
    except Exception:
        pass
    return None


class Command(BaseCommand):
    help = 'Re-descarga imágenes corruptas desde scraped_modaverse.json'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run',  action='store_true',
                            help='Muestra qué haría sin descargar')
        parser.add_argument('--min-size', type=int, default=500,
                            help='Umbral en bytes — archivos menores se reparan (default: 500)')
        parser.add_argument('--workers',  type=int, default=4,
                            help='Hilos paralelos de descarga (default: 4)')

    def handle(self, *args, **options):
        dry      = options['dry_run']
        min_size = options['min_size']
        workers  = options['workers']

        products_dir = Path(settings.MEDIA_ROOT) / 'products'

        # ── 1. Archivos corruptos en disco ────────────────────────────────────
        corrupted_files = [
            f for f in products_dir.iterdir()
            if f.is_file() and f.stat().st_size < min_size
        ]
        self.stdout.write(f'{len(corrupted_files)} archivos corruptos (< {min_size} bytes)\n')

        if not corrupted_files:
            self.stdout.write(self.style.SUCCESS('Sin corruptos — nada que hacer.'))
            return

        # ── 2. Mapa supplier_url → images desde JSON ─────────────────────────
        json_path = Path(__file__).resolve().parents[4] / 'scraped_modaverse.json'
        if not json_path.exists():
            self.stdout.write(self.style.ERROR(f'No se encontró {json_path}'))
            return

        self.stdout.write('Cargando JSON...')
        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        url_to_images = {
            p['url']: p.get('images', [])
            for p in data.get('products', [])
            if p.get('url')
        }
        del data
        self.stdout.write(f'  {len(url_to_images)} productos en JSON\n')

        # ── 3. Cruzar archivos corruptos con registros en BD ──────────────────
        jobs = []  # (file_path, img_url)

        for f in corrupted_files:
            relative = f'products/{f.name}'
            try:
                pi = ProductImage.objects.select_related('product').get(image=relative)
            except ProductImage.DoesNotExist:
                continue

            imgs = url_to_images.get(pi.product.supplier_url, [])
            if not imgs or pi.display_order >= len(imgs):
                continue

            img_url = imgs[pi.display_order]
            if img_url:
                jobs.append((f, img_url, pi.product.sku, pi.display_order))

        self.stdout.write(f'{len(jobs)} imágenes con URL disponible para reparar')
        skipped = len(corrupted_files) - len(jobs)
        if skipped:
            self.stdout.write(self.style.WARNING(f'{skipped} sin URL en JSON — se omiten'))

        if dry:
            self.stdout.write(self.style.SUCCESS('\nDRY RUN — no se descargó nada.'))
            return

        # ── 4. Descargar en paralelo ──────────────────────────────────────────
        ok = failed = 0

        def _fix_one(args):
            file_path, img_url, sku, order = args
            content = _download(img_url, min_size)
            if content:
                file_path.write_bytes(content)
                return True, sku, order, len(content)
            return False, sku, order, 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fix_one, job): job for job in jobs}
            for i, future in enumerate(as_completed(futures), 1):
                success, sku, order, size = future.result()
                if success:
                    ok += 1
                    if ok <= 5 or ok % 100 == 0:
                        self.stdout.write(f'  [{i}/{len(jobs)}] ✓ {sku}[{order}] — {size//1024} KB')
                else:
                    failed += 1
                    self.stdout.write(self.style.WARNING(
                        f'  [{i}/{len(jobs)}] ✗ {sku}[{order}]'
                    ))

        self.stdout.write(self.style.SUCCESS(
            f'\n✅ {ok} reparadas, {failed} fallaron, {skipped} sin URL en JSON'
        ))
