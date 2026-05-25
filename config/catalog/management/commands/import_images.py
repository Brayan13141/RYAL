"""
Descarga imágenes de productos desde scraped_modaverse.json.
Procesa en lotes (chunks) para evitar OOM en servidores con poca RAM.

Uso:
    python manage.py import_images
    python manage.py import_images --only gorras
    python manage.py import_images --only camisetas --workers 3
    python manage.py import_images --chunk-size 200
    python manage.py import_images --max-per-product 10
    python manage.py import_images --force
    python manage.py import_images --since 2026-05-21   # solo productos creados desde esa fecha
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

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

# Fragmento del slug de categoría raíz — se usa con __contains para capturar
# tanto la categoría padre como sus subcategorías hijas.
_CATEGORY_SLUG_HINT = {
    'gorras':    'gorra',
    'camisetas': 'camiseta',
    'sudaderas': 'sudadera',
    'airpods':   'electronica',
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


def _iter_chunks(queryset, chunk_size):
    """Itera un queryset en lotes sin cargar todo en memoria."""
    last_pk = 0
    while True:
        chunk = list(queryset.filter(pk__gt=last_pk).order_by('pk')[:chunk_size])
        if not chunk:
            break
        yield chunk
        last_pk = chunk[-1].pk


class Command(BaseCommand):
    help = 'Descarga imágenes para productos desde scraped_modaverse.json (por lotes)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--only',
            choices=list(_CATEGORY_SLUG_HINT.keys()),
            help='Limitar a una categoría (incluye subcategorías)',
        )
        parser.add_argument(
            '--workers', type=int, default=3,
            help='Hilos paralelos de descarga (default: 3)',
        )
        parser.add_argument(
            '--max-per-product', type=int, default=250,
            help='Máximo de imágenes por producto (default: 250)',
        )
        parser.add_argument(
            '--chunk-size', type=int, default=300,
            help='Productos por lote en memoria (default: 300)',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Re-descargar aunque ya tenga imágenes',
        )
        parser.add_argument(
            '--fill-gaps', action='store_true',
            help='También procesar productos que ya tienen algunas imágenes pero menos que el máximo '
                 '(por defecto solo procesa productos sin ninguna imagen)',
        )
        parser.add_argument(
            '--since',
            metavar='YYYY-MM-DD',
            help='Solo productos creados en o después de esta fecha',
        )

    def handle(self, *args, **options):
        only       = options['only']
        workers    = options['workers']
        max_imgs   = options['max_per_product']
        chunk_size = options['chunk_size']
        force      = options['force']
        fill_gaps  = options['fill_gaps']
        since      = options['since']

        # ── Cargar mapa supplier_url → images desde JSON ──────────────────────
        json_path = Path(__file__).resolve().parents[4] / 'scraped_modaverse.json'
        if not json_path.exists():
            self.stdout.write(self.style.ERROR(f'No se encontró {json_path}'))
            return

        self.stdout.write('Cargando JSON...')
        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        url_to_images = {
            p['url']: p['images']
            for p in data.get('products', [])
            if p.get('url') and p.get('images')
        }
        self.stdout.write(f'  {len(url_to_images)} productos con imágenes en JSON')
        del data  # liberar memoria del JSON completo

        # ── Construir queryset base (sin cargar en memoria) ───────────────────
        qs = Product.objects.select_related('category__parent').annotate(img_count=Count('images'))
        if only:
            hint = _CATEGORY_SLUG_HINT[only]
            qs = qs.filter(
                Q(category__slug__contains=hint) |
                Q(category__parent__slug__contains=hint)
            )
        if not force:
            if fill_gaps:
                # Rellena imágenes faltantes para productos que ya tienen algunas
                qs = qs.filter(img_count__lt=max_imgs)
            else:
                # Por defecto: solo productos sin ninguna imagen (no re-procesa existentes)
                qs = qs.filter(img_count=0)
        if since:
            qs = qs.filter(created_at__date__gte=since)

        total = qs.count()
        if force:
            mode_label = f'(force — todos, hasta {max_imgs} imgs)'
        elif fill_gaps:
            mode_label = f'(fill-gaps — con menos de {max_imgs} imgs)'
        else:
            mode_label = '(solo sin imágenes)'
        self.stdout.write(
            f'  {total} productos {mode_label}'
            + (f' en {only}' if only else '')
            + (f' desde {since}' if since else '')
        )

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nada que descargar.'))
            return

        self.stdout.write(
            f'\nDescargando en lotes de {chunk_size} · {workers} hilos · {total} productos total\n'
        )

        ok = failed = skipped = missing = processed = 0

        for chunk in _iter_chunks(qs, chunk_size):
            jobs = []
            for p in chunk:
                imgs = url_to_images.get(p.supplier_url)
                if imgs:
                    jobs.append((p, imgs))
                else:
                    missing += 1

            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_to_prod = {
                    pool.submit(_save_images_for_product, prod, imgs, max_imgs): prod
                    for prod, imgs in jobs
                }
                for future in as_completed(future_to_prod):
                    prod = future_to_prod[future]
                    processed += 1
                    try:
                        saved = future.result()
                        if saved:
                            ok += 1
                            if processed % 200 == 0 or processed <= 5:
                                self.stdout.write(
                                    f'  [{processed}/{total}] {prod.sku} — {saved} imgs'
                                )
                        else:
                            skipped += 1
                    except Exception as e:
                        failed += 1
                        self.stdout.write(self.style.WARNING(f'  [!] {prod.sku}: {e}'))

            self.stdout.write(
                f'  Lote completado — acumulado: {ok} OK · {skipped} sin imgs · {failed} errores'
            )

        if missing:
            self.stdout.write(self.style.WARNING(f'  {missing} productos sin URL en JSON'))

        self.stdout.write(
            self.style.SUCCESS(
                f'\n✅ Imágenes importadas: {ok} productos OK, {skipped} sin imgs descargables, {failed} errores'
            )
        )
