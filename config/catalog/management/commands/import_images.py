"""
Descarga imágenes de productos desde scraped_modaverse.json.
Usa descargas paralelas para mayor velocidad.

Uso:
    python manage.py import_images
    python manage.py import_images --only gorras
    python manage.py import_images --only camisetas --workers 20
    python manage.py import_images --max-per-product 2
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import Product, ProductImage

HEADERS = {
    'Referer': 'https://www.modaverse.vip/',
    'Origin': 'https://www.modaverse.vip',
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}

_CATEGORY_SLUGS = {
    'gorras':     'gorras',
    'camisetas':  'camisetas',
    'sudaderas':  'sudaderas',
    'airpods':    'airpods',
}


def _ext_from_url_or_content_type(url: str, content_type: str) -> str:
    from urllib.parse import urlparse
    path = urlparse(url).path
    filename = path.rsplit('/', 1)[-1]
    if '.' in filename:
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            return ext
    if 'png' in content_type:
        return 'png'
    if 'webp' in content_type:
        return 'webp'
    return 'jpg'


def _download_one(url: str, timeout: int = 20):
    try:
        r = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
        if r.status_code == 200 and len(r.content) > 500:
            ext = _ext_from_url_or_content_type(url, r.headers.get('content-type', ''))
            return r.content, ext
    except Exception:
        pass
    return None, None


def _save_images_for_product(product, image_urls, max_imgs):
    existing_count = product.images.count()
    if existing_count >= max_imgs:
        return 0
    saved = 0
    for order, url in enumerate(image_urls[existing_count:max_imgs], start=existing_count):
        if not url:
            continue
        content, ext = _download_one(url)
        if not content:
            continue
        pi = ProductImage(
            product=product,
            is_cover=(order == 0 and existing_count == 0),
            display_order=order,
        )
        pi.image.save(f'{product.sku}_{order}.{ext}', ContentFile(content), save=True)
        saved += 1
    return saved


class Command(BaseCommand):
    help = 'Descarga imágenes para productos sin imágenes desde scraped_modaverse.json'

    def add_arguments(self, parser):
        parser.add_argument(
            '--only',
            choices=list(_CATEGORY_SLUGS.keys()),
            help='Limitar a una categoría',
        )
        parser.add_argument(
            '--workers', type=int, default=10,
            help='Hilos paralelos de descarga (default: 10)',
        )
        parser.add_argument(
            '--max-per-product', type=int, default=150,
            help='Máximo de imágenes por producto (default: 150)',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Re-descargar aunque ya tenga imágenes',
        )

    def handle(self, *args, **options):
        only      = options['only']
        workers   = options['workers']
        max_imgs  = options['max_per_product']
        force     = options['force']

        # ── Cargar mapa supplier_url → images desde JSON ──────────────────────
        json_path = Path(__file__).resolve().parents[4] / 'scraped_modaverse.json'
        if not json_path.exists():
            self.stdout.write(self.style.ERROR(f'No se encontró {json_path}'))
            return

        self.stdout.write(f'Cargando JSON...')
        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        url_to_images = {
            p['url']: p['images']
            for p in data.get('products', [])
            if p.get('url') and p.get('images')
        }
        self.stdout.write(f'  {len(url_to_images)} productos con imágenes en JSON')

        # ── Seleccionar productos a procesar ──────────────────────────────────
        qs = Product.objects.select_related('category').annotate(img_count=Count('images'))
        if only:
            slug = _CATEGORY_SLUGS[only]
            qs = qs.filter(category__slug=slug)
        if not force:
            qs = qs.filter(img_count__lt=max_imgs)

        products = list(qs.order_by('sku'))
        self.stdout.write(f'  {len(products)} productos con menos de {max_imgs} imágenes' + (f' en {only}' if only else ''))

        if not products:
            self.stdout.write(self.style.SUCCESS('Nada que descargar.'))
            return

        # ── Preparar trabajos ─────────────────────────────────────────────────
        jobs = []
        missing_in_json = 0
        for p in products:
            imgs = url_to_images.get(p.supplier_url)
            if imgs:
                jobs.append((p, imgs))
            else:
                missing_in_json += 1

        if missing_in_json:
            self.stdout.write(
                self.style.WARNING(f'  {missing_in_json} productos sin URL en JSON (se saltan)')
            )

        self.stdout.write(f'\nDescargando imágenes para {len(jobs)} productos ({workers} hilos)...\n')

        ok = failed = skipped = 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_prod = {
                pool.submit(_save_images_for_product, prod, imgs, max_imgs): prod
                for prod, imgs in jobs
            }
            for i, future in enumerate(as_completed(future_to_prod), 1):
                prod = future_to_prod[future]
                try:
                    saved = future.result()
                    if saved:
                        ok += 1
                        if i % 50 == 0 or i <= 5:
                            self.stdout.write(f'  [{i}/{len(jobs)}] {prod.sku} — {saved} imgs')
                    else:
                        skipped += 1
                except Exception as e:
                    failed += 1
                    self.stdout.write(
                        self.style.WARNING(f'  [!] {prod.sku}: {e}')
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f'\n✅ Imágenes importadas: {ok} productos OK, {skipped} sin imgs descargables, {failed} errores'
            )
        )
