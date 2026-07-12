"""
Repara ProductImages guardadas con path incorrecto o nombre no normalizado.

Casos que corrige:
  1. Bug de directorio anidado: URL sin extensión generaba jerarquía en media/products/
     (ej. products/api.modaverse.vip/kkd_boot/.../ZA-091_XYZ)
  2. Extensión con sufijo extra: URLs de modaverse incluyen timestamp tras la ext
     (ej. products/XT_0102836.jpg_1774322093986 → products/RYL-SKU-001_0.jpg)

En ambos casos el archivo existe — solo está mal nombrado.

Uso:
    python manage.py repair_images
    python manage.py repair_images --dry-run              # solo muestra qué haría
    python manage.py repair_images --redownload-missing   # re-descarga imágenes faltantes
    python manage.py repair_images --redownload-missing --dry-run
"""
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import httpx

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from catalog.models import Product, ProductImage
from catalog.modaverse import pid_from_url

_DOWNLOAD_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://www.modaverse.vip/',
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
}


def _clean_url(url: str) -> str:
    """Elimina caracteres de control y percent-encoda path no-ASCII."""
    url = re.sub(r'[\r\n\t]', '', url).strip()
    if not url:
        return url
    try:
        p = urlparse(url)
        encoded_path = quote(p.path, safe='/:@!$&\'()*+,;=')
        return urlunparse(p._replace(path=encoded_path))
    except Exception:
        return url


def _download_image(url: str, timeout: int = 20):
    url = _clean_url(url)
    if not url:
        return None
    try:
        r = httpx.get(url, headers=_DOWNLOAD_HEADERS, timeout=timeout, follow_redirects=True)
        if r.status_code == 200 and r.content:
            return r.content
        return None
    except Exception:
        return None


def _normalize_image_url(raw: str) -> str:
    """Reconstruye URLs parciales de imágenes modaverse que no tienen base/fecha."""
    if not raw or raw.startswith('http'):
        return raw
    ts_match = re.search(r'(\d{13})', raw)
    if ts_match:
        ts_ms = int(ts_match.group(1))
        date_str = datetime.utcfromtimestamp(ts_ms / 1000).strftime('%Y%m%d')
        return f"https://api.modaverse.vip/kkd_boot/file/static/{date_str}/{raw}"
    return raw


class Command(BaseCommand):
    help = 'Repara imágenes con path incorrecto; opcionalmente re-descarga imágenes faltantes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra qué haría sin ejecutar cambios'
        )
        parser.add_argument(
            '--redownload-missing', action='store_true',
            help='Descarga imágenes para productos sin ninguna imagen (usa scraped_modaverse.json)'
        )

    def handle(self, *args, **options):
        dry = options['dry_run']
        media_root = Path(settings.MEDIA_ROOT)

        if dry:
            self.stdout.write(self.style.WARNING('DRY RUN — no se harán cambios\n'))

        repaired = skipped = errors = 0

        for pi in ProductImage.objects.select_related('product').order_by('product__sku', 'display_order'):
            try:
                img_path = Path(pi.image.path)
            except Exception:
                continue

            if not img_path.exists():
                self.stdout.write(f'  [!] Archivo no existe: {pi.image.name}')
                errors += 1
                continue

            # Si ya tiene extensión de imagen válida -> ok
            if img_path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                skipped += 1
                continue

            # Archivo existe pero con extensión no válida (p.ej. .jpg_1774322093986 o sin ext)
            # Determinar extensión real: si el stem contiene ".jpg"/".png"/etc., usar esa
            stem = img_path.name  # ej. "XT_0102836.jpg_1774322093986"
            real_ext = 'jpg'
            for candidate in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                if candidate in stem.lower():
                    real_ext = candidate.lstrip('.')
                    break

            sku = pi.product.sku
            order = pi.display_order
            new_name = f'products/{sku}_{order}.{real_ext}'
            new_path = media_root / new_name

            self.stdout.write(f'  >> {sku}_{order}: {img_path.name} -> {new_name}')

            if not dry:
                if new_path.exists() and new_path != img_path:
                    self.stdout.write(
                        self.style.WARNING(f'  [!] {new_name} ya existe — saltando {img_path.name}')
                    )
                    errors += 1
                    continue

                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(img_path, new_path)

                # Caso 1: directorio anidado (bug original) → borrar árbol
                parts = img_path.relative_to(media_root / 'products').parts
                if len(parts) > 1:
                    garbage_dir = media_root / 'products' / parts[0]
                    if garbage_dir.is_dir() and garbage_dir != new_path.parent:
                        shutil.rmtree(garbage_dir)
                # Caso 2: archivo plano con extensión incorrecta → borrar el original
                elif img_path.is_file() and img_path != new_path:
                    img_path.unlink()

                pi.image.name = new_name
                pi.save(update_fields=['image'])

            repaired += 1

        suffix = ' (simulado)' if dry else ''
        self.stdout.write(
            self.style.SUCCESS(
                f'\nOK: {repaired} reparadas{suffix}, {skipped} ya correctas, {errors} errores'
            )
        )

        if options['redownload_missing']:
            self._redownload_missing(dry)

    def _redownload_missing(self, dry: bool):
        self.stdout.write('\n── Re-descargando imágenes faltantes ──')

        json_path = Path(__file__).resolve().parents[4] / 'scraped_modaverse.json'
        if not json_path.exists():
            self.stdout.write(self.style.WARNING('  ⚠ No se encontró scraped_modaverse.json'))
            return

        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        # Mapa pid → images[]. Las URLs del JSON (#/product/...?pid=X) NUNCA
        # coinciden con el formato normalizado en BD (#/proinfo/{pid}, ver
        # decisión "supplier_url formato #/proinfo/{pid}") — comparar por pid
        # extraído, no por URL exacta, o esto nunca encuentra nada.
        pid_to_images = {}
        for p in data.get('products', []):
            pid = pid_from_url(p.get('url'))
            if pid:
                pid_to_images[pid] = p.get('images', [])
        self.stdout.write(f'  JSON cargado: {len(pid_to_images)} productos')

        # Productos en BD sin ninguna imagen
        sin_img = Product.objects.filter(images__isnull=True).select_related('category')
        total = sin_img.count()
        self.stdout.write(f'  Productos sin imágenes en BD: {total}')

        fixed = failed = skipped = 0

        for product in sin_img:
            pid = pid_from_url(product.supplier_url)
            raw_images = pid_to_images.get(pid, []) if pid else []
            if not raw_images:
                skipped += 1
                continue

            # Expandir URLs que vengan unidas por \n en el JSON y normalizar parciales
            expanded = []
            for raw in raw_images:
                if not raw:
                    continue
                for part in re.split(r'[\r\n]+', raw):
                    part = part.strip()
                    if part:
                        expanded.append(_normalize_image_url(part))

            images = [_clean_url(u) for u in expanded if u and u.startswith('http')]

            if not images:
                skipped += 1
                continue

            self.stdout.write(f'  {product.sku} — {len(images)} imgs disponibles')

            if dry:
                fixed += 1
                continue

            downloaded = 0
            for order, img_url in enumerate(images[:4]):
                img_bytes = _download_image(img_url)
                if not img_bytes:
                    continue

                path = urlparse(img_url).path
                fname = path.rsplit('/', 1)[-1]
                ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else 'jpg'
                if not (ext.isalpha() and 2 <= len(ext) <= 5):
                    ext = 'jpg'

                pi = ProductImage(
                    product=product,
                    is_cover=(order == 0),
                    display_order=order,
                )
                pi.image.save(f"{product.sku}_{order}.{ext}", ContentFile(img_bytes), save=True)
                downloaded += 1

            if downloaded:
                fixed += 1
            else:
                failed += 1

        suffix = ' (simulado)' if dry else ''
        self.stdout.write(
            self.style.SUCCESS(
                f'\n  Re-descarga{suffix}: {fixed} OK, {failed} fallaron, '
                f'{skipped} sin URL en JSON'
            )
        )
