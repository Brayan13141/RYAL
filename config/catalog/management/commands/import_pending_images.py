"""
Descarga la imagen de portada de los PendingProduct que aún no tienen imagen local.
Solo toca productos con status='pending' y cover_image vacío.

Uso:
    python manage.py import_pending_images
    python manage.py import_pending_images --workers 4
    python manage.py import_pending_images --limit 50
    python manage.py import_pending_images --backfill   # parcha raw_data desde JSON antes de descargar
"""
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import httpx
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db.models import Q
from PIL import Image, UnidentifiedImageError

from catalog.models import PendingProduct
from catalog.modaverse import read_modaverse_json, pid_from_url

_ALLOWED_HOSTS = {
    'api.modaverse.vip',
    'img.modaverse.vip',
    'modaverse.vip',
    'kkd-file.oss-cn-hangzhou.aliyuncs.com',  # CDN alternativo que usa Modaverse
}
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer':    'https://www.modaverse.vip/',
    'Accept':     'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
}
_TIMEOUT     = 15
_MAX_BYTES   = 10 * 1024 * 1024  # 10 MB tope — las fotos de producto no superan esto
_MIN_BYTES   = 1_000             # descartas respuestas vacías/error HTML


def _validate_url(url: str) -> str:
    """Devuelve la URL si es segura, lanza ValueError si no."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f'esquema no permitido: {parsed.scheme!r}')
    host = parsed.hostname or ''
    # Acepta el host exacto o cualquier subdominio de los hosts permitidos
    if not any(host == h or host.endswith('.' + h) for h in _ALLOWED_HOSTS):
        raise ValueError(f'host no permitido: {host!r}')
    return url


def _verify_image(data: bytes) -> str:
    """Verifica con Pillow que los bytes son una imagen válida. Retorna la extensión."""
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        fmt = (img.format or 'JPEG').lower()
        return {'jpeg': 'jpg', 'png': 'png', 'webp': 'webp', 'gif': 'gif'}.get(fmt, 'jpg')
    except UnidentifiedImageError:
        raise ValueError('los bytes descargados no son una imagen válida')


def _download(pk: int, url: str) -> tuple[int, bool, str]:
    """Descarga y valida la imagen, la guarda en PendingProduct. Retorna (pk, ok, msg)."""
    try:
        _validate_url(url)

        resp = httpx.get(
            url,
            headers=_HEADERS,
            timeout=_TIMEOUT,
            follow_redirects=False,  # sin seguir redirects para evitar SSRF via Location
        )
        resp.raise_for_status()

        data = resp.content
        if len(data) < _MIN_BYTES:
            return pk, False, f'respuesta demasiado pequeña ({len(data)} B) — posible HTML de error'
        if len(data) > _MAX_BYTES:
            return pk, False, f'imagen demasiado grande ({len(data) // 1024} KB)'

        ext = _verify_image(data)

        # Re-check con select_for_update para evitar race con otros workers
        # o con imágenes subidas manualmente entre que se construyó el queryset
        # y este momento.
        from django.db import transaction
        with transaction.atomic():
            pending = PendingProduct.objects.select_for_update().get(pk=pk)
            if pending.cover_image:
                return pk, False, 'ya tiene imagen (subida manualmente o por otro worker)'
            pending.cover_image.save(f'{pk}.{ext}', ContentFile(data), save=True)
        return pk, True, f'{pk}.{ext}'

    except ValueError as e:
        return pk, False, str(e)
    except httpx.HTTPStatusError as e:
        return pk, False, f'HTTP {e.response.status_code}'
    except Exception as e:
        return pk, False, str(e)[:80]


class Command(BaseCommand):
    help = 'Descarga imagen de portada para PendingProducts sin imagen local.'

    def add_arguments(self, parser):
        parser.add_argument('--workers', type=int, default=4,
                            help='Hilos paralelos (default: 4).')
        parser.add_argument('--limit', type=int, default=None,
                            help='Máximo de productos a procesar en esta corrida.')
        parser.add_argument('--backfill', action='store_true',
                            help='Lee scraped_modaverse.json y parcha raw_data.image_url '
                                 'para productos sin URL antes de descargar.')

    def _backfill_from_json(self):
        """Parcha raw_data['image_url'] usando el JSON scrapeado para productos pre-s8."""
        data = read_modaverse_json()
        if not data:
            self.stdout.write(self.style.WARNING(
                '  ⚠ scraped_modaverse.json no encontrado — backfill omitido'
            ))
            return

        pid_to_img = {
            p['sku']: p['images'][0]
            for p in data.get('products', [])
            if p.get('sku') and p.get('images')
        }
        if not pid_to_img:
            self.stdout.write('  Backfill: JSON sin imágenes, nada que parchar.')
            return

        qs = PendingProduct.objects.filter(
            status='pending', cover_image='',
        ).filter(
            Q(raw_data__image_url='') | Q(raw_data__image_url__isnull=True)
        )

        patched = 0
        for pp in qs:
            pid = pid_from_url(pp.supplier_url or '')
            img_url = pid_to_img.get(pid, '')
            if img_url:
                pp.raw_data = {**(pp.raw_data or {}), 'image_url': img_url}
                pp.save(update_fields=['raw_data'])
                patched += 1

        self.stdout.write(f'  Backfill: {patched} productos actualizados con image_url desde JSON.')

    def handle(self, *args, **options):
        workers = options['workers']
        limit   = options['limit']

        if options['backfill']:
            self._backfill_from_json()

        qs = (PendingProduct.objects
              .filter(status='pending', cover_image='')
              .exclude(raw_data__image_url='')
              .only('pk', 'raw_data', 'cover_image'))

        if limit:
            qs = qs[:limit]

        tasks = [(p.pk, p.raw_data.get('image_url', '')) for p in qs if p.raw_data.get('image_url')]

        if not tasks:
            self.stdout.write('  Sin pendientes con URL de imagen. Nada que hacer.')
            return

        self.stdout.write(f'  Descargando imágenes para {len(tasks)} productos (workers={workers})...')
        ok = fail = 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_download, pk, url): pk for pk, url in tasks}
            for future in as_completed(futures):
                pk, success, msg = future.result()
                if success:
                    ok += 1
                    if options['verbosity'] >= 2:
                        self.stdout.write(f'    ✓ {pk} → {msg}')
                else:
                    fail += 1
                    self.stdout.write(self.style.WARNING(f'    ✗ {pk}: {msg}'))

        self.stdout.write(
            self.style.SUCCESS(f'  ✓ {ok} descargadas · {fail} errores')
        )
