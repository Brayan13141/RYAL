"""
Descarga imágenes para productos TN2 (Calzado/yupoo_pf) que existen en BD sin imágenes.

Uso:
    python manage.py download_yupoo_images
    python manage.py download_yupoo_images --dry-run
    python manage.py download_yupoo_images --max-per-product 2
"""
import json
import re
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import httpx

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from catalog.models import Product, ProductImage

REFERER = 'https://putianshoefactory.x.yupoo.com'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Referer': REFERER,
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
}


def _clean_url(url: str) -> str:
    url = re.sub(r'[\r\n\t]', '', url).strip()
    if not url:
        return url
    p = urlparse(url)
    return urlunparse(p._replace(path=quote(p.path, safe='/:@!$&\'()*+,;=')))


def _get_ext(url: str) -> str:
    path = urlparse(url).path
    fname = path.rsplit('/', 1)[-1]
    if '.' in fname:
        ext = fname.rsplit('.', 1)[-1].lower()
        if ext.isalpha() and 2 <= len(ext) <= 5:
            return ext
    return 'jpg'


def _download(url: str, timeout: int = 20) -> bytes | None:
    url = _clean_url(url)
    if not url:
        return None
    try:
        r = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
        return r.content if r.status_code == 200 else None
    except Exception as e:
        return None


class Command(BaseCommand):
    help = 'Descarga imágenes para productos TN2 (Calzado) sin imágenes en BD'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra qué haría sin descargar nada',
        )
        parser.add_argument(
            '--max-per-product', type=int, default=4,
            help='Máximo de imágenes a descargar por producto (default: 4)',
        )

    def handle(self, *args, **options):
        dry     = options['dry_run']
        max_img = options['max_per_product']

        if dry:
            self.stdout.write(self.style.WARNING('DRY RUN — no se descargarán imágenes\n'))

        json_path = Path(__file__).resolve().parents[4] / 'scraped_yupoo_pf.json'
        if not json_path.exists():
            self.stdout.write(self.style.ERROR(f'No se encontró {json_path}'))
            return

        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        url_to_images = {p['url']: p.get('images', []) for p in data.get('products', [])}
        self.stdout.write(f'JSON cargado: {len(url_to_images)} productos')

        sin_img = Product.objects.filter(sku__startswith='RYL-TN2-', images__isnull=True)
        total   = sin_img.count()
        self.stdout.write(f'Productos TN2 sin imágenes: {total}\n')

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nada que hacer — todos los TN2 tienen imágenes'))
            return

        ok = fail = skip = 0

        for product in sin_img:
            images = url_to_images.get(product.supplier_url, [])
            if not images:
                self.stdout.write(f'  {product.sku} — sin URL en JSON, saltando')
                skip += 1
                continue

            self.stdout.write(f'  {product.sku} — {product.name[:45]}')

            if dry:
                ok += 1
                continue

            downloaded = 0
            for order, img_url in enumerate(images[:max_img]):
                if not img_url:
                    continue
                img_bytes = _download(img_url)
                if not img_bytes:
                    self.stdout.write(f'    [{order}] ✗ {img_url[:70]}')
                    continue
                ext = _get_ext(img_url)
                pi  = ProductImage(
                    product=product,
                    is_cover=(order == 0),
                    display_order=order,
                )
                pi.image.save(f'{product.sku}_{order}.{ext}', ContentFile(img_bytes), save=True)
                downloaded += 1
                self.stdout.write(f'    [{order}] ✓')

            if downloaded:
                ok += 1
            else:
                fail += 1

        suffix = ' (simulado)' if dry else ''
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Listo{suffix}: {ok} con imágenes, {fail} fallaron, {skip} sin URL en JSON'
        ))
