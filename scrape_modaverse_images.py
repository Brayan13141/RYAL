"""
Enriquece scraped_modaverse.json con imágenes adicionales del detalle de producto.

El scraper principal (scrape_modaverse_final.py) solo captura 1 imagen por producto
(thumbnail del listado). Este script descubre el endpoint de detalle de la API de
modaverse y lo usa para obtener todas las imágenes de cada producto.

Estrategia:
  1. Abre UNA página de producto con DynamicFetcher para capturar la llamada XHR
     al endpoint de detalle y entender el formato de respuesta.
  2. Usa httpx para batch-fetch del detalle de todos los productos que tienen
     menos de --min-images imágenes en el JSON.
  3. Actualiza scraped_modaverse.json en disco con las imágenes encontradas.

Uso:
    python scrape_modaverse_images.py                        # todos los productos con <2 imgs
    python scrape_modaverse_images.py --min-images 5         # productos con <5 imgs
    python scrape_modaverse_images.py --cat "Calidad 1:1"    # solo esa categoría padre
    python scrape_modaverse_images.py --cat "Calidad G5"
    python scrape_modaverse_images.py --limit 200            # máx 200 productos
    python scrape_modaverse_images.py --dry-run              # muestra cuántos procesaría
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
try:
    from scrapling.fetchers import DynamicFetcher
    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False

OUTPUT_PATH = Path(__file__).parent / 'scraped_modaverse.json'
API_BASE    = 'https://api.modaverse.vip/kkd_boot'

HEADERS = {
    'Content-Type':    'application/json',
    'Referer':         'https://www.modaverse.vip/',
    'Origin':          'https://www.modaverse.vip',
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'es-MX,es;q=0.9',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}

# Endpoints de detalle conocidos — se prueban en orden hasta que uno responda
_DETAIL_ENDPOINTS = [
    '/product/getProductDetailById',
    '/product/getProductDetail',
    '/product/detailInfo',
    '/product/getProductById',
    '/product/queryProductDetail',
    '/product/getDetail',
]


def log(msg: str):
    print(f'[imgs] {msg}', flush=True)


def _normalize_image_url(raw: str) -> str:
    if not raw:
        return ''
    if raw.startswith('http'):
        return raw
    match = re.search(r'(\d{13})', raw)
    if not match:
        return raw
    ts_ms    = int(match.group(1))
    date_str = datetime.utcfromtimestamp(ts_ms / 1000).strftime('%Y%m%d')
    return f'https://api.modaverse.vip/kkd_boot/file/static/{date_str}/{raw}'


def _extract_images_from_response(data: dict) -> list[str]:
    """Extrae y normaliza imageList de la respuesta de la API de detalle."""
    inner = data.get('data') or {}
    if isinstance(inner, list):
        inner = inner[0] if inner else {}

    img_raw = inner.get('imageList') or inner.get('imgList') or inner.get('images') or []
    if isinstance(img_raw, list):
        imgs = [_normalize_image_url(str(u).strip()) for u in img_raw if u]
    elif isinstance(img_raw, str) and img_raw:
        imgs = [_normalize_image_url(u.strip()) for u in re.split(r'[,\r\n]+', img_raw) if u.strip()]
    else:
        imgs = []

    return [u for u in imgs if u.startswith('http')]


def _try_httpx_endpoints(client: httpx.Client, product_id: str) -> tuple[str | None, list[str]]:
    """Prueba endpoints conocidos con httpx. Retorna (endpoint_url, images)."""
    for path in _DETAIL_ENDPOINTS:
        url = API_BASE + path
        try:
            r = client.post(url, json={'productId': product_id}, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            if not data.get('success'):
                continue
            imgs = _extract_images_from_response(data)
            if imgs:
                log(f'  Endpoint encontrado: {url} → {len(imgs)} imgs para {product_id}')
                return url, imgs
        except Exception:
            continue
    return None, []


def _discover_via_dynamic_fetcher(sample_url: str) -> str | None:
    """Usa DynamicFetcher en una página de producto para capturar el XHR de detalle."""
    if not HAS_SCRAPLING:
        log('  scrapling no instalado — saltando discovery con browser')
        return None

    log(f'  Abriendo {sample_url} con DynamicFetcher...')
    try:
        page = DynamicFetcher.fetch(
            sample_url,
            network_idle=True,
            wait=5000,
            timeout=60000,
            capture_xhr=r'.*kkd_boot.*',
            headless=True,
        )
    except Exception as e:
        log(f'  DynamicFetcher error: {e}')
        return None

    log(f'  XHR capturados: {len(page.captured_xhr)}')
    for xhr in page.captured_xhr:
        url  = getattr(xhr, 'url', '') or ''
        body = getattr(xhr, 'body', b'') or b''
        if 'getUserPage' in url or not body or 'kkd_boot' not in url:
            continue
        try:
            data = json.loads(body.decode('utf-8'))
            imgs = _extract_images_from_response(data)
            if imgs:
                endpoint = url.split('?')[0]
                log(f'  Endpoint de detalle encontrado via XHR: {endpoint}')
                log(f'  Muestra de imágenes: {imgs[:2]}')
                return endpoint
        except Exception:
            continue

    log('  No se encontró endpoint de detalle en XHRs')
    return None


def _fetch_detail(client: httpx.Client, endpoint: str, product_id: str, retries: int = 2) -> list[str]:
    for attempt in range(retries + 1):
        try:
            r = client.post(endpoint, json={'productId': product_id}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data.get('success'):
                    return _extract_images_from_response(data)
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 ** attempt)
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--min-images', type=int, default=2,
                        help='Procesar productos con menos de N imágenes (default: 2)')
    parser.add_argument('--cat', type=str, default='',
                        help='Filtrar por nombre de categoría padre (ej: "Calidad 1:1")')
    parser.add_argument('--limit', type=int, default=0,
                        help='Máximo de productos a procesar (0 = sin límite)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Muestra cuántos procesaría sin hacer nada')
    parser.add_argument('--delay', type=float, default=0.2,
                        help='Segundos entre requests (default: 0.2)')
    args = parser.parse_args()

    # ── Cargar JSON ───────────────────────────────────────────────────────────
    if not OUTPUT_PATH.exists():
        log(f'ERROR: no se encontró {OUTPUT_PATH}')
        sys.exit(1)

    log(f'Cargando {OUTPUT_PATH}...')
    with open(OUTPUT_PATH, encoding='utf-8') as f:
        data = json.load(f)

    products = data.get('products', [])
    log(f'  {len(products)} productos en JSON')

    # Mapa categoría padre ID → nombre
    cat_parent_name: dict[str, str] = {}
    for cat in data.get('categories', []):
        pname = cat.get('name_es', '')
        cat_parent_name[cat['id']] = pname
        for sub in cat.get('subcategories', []):
            cat_parent_name[sub['id']] = pname

    # ── Seleccionar productos a procesar ──────────────────────────────────────
    to_process = []
    for p in products:
        if len(p.get('images', [])) >= args.min_images:
            continue
        if args.cat:
            pname = cat_parent_name.get(p.get('category_id', ''), p.get('category', ''))
            if args.cat.lower() not in pname.lower():
                continue
        to_process.append(p)

    if args.limit:
        to_process = to_process[:args.limit]

    log(f'\n{len(to_process)} productos con <{args.min_images} imágenes' +
        (f' en "{args.cat}"' if args.cat else '') + ' a procesar')

    if args.dry_run or not to_process:
        if not to_process:
            log('Nada que hacer.')
        return

    # ── Descubrir endpoint de detalle ─────────────────────────────────────────
    sample_pid = to_process[0].get('sku') or to_process[0].get('category_id')
    sample_url = to_process[0].get('url', '')

    log(f'\nDescubriendo endpoint de detalle con producto: {sample_pid}')

    detail_endpoint = None

    # Primero intentar httpx directamente (más rápido que DynamicFetcher)
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        detail_endpoint, _ = _try_httpx_endpoints(client, sample_pid)

    # Fallback: DynamicFetcher XHR capture
    if not detail_endpoint and sample_url and HAS_SCRAPLING:
        detail_endpoint = _discover_via_dynamic_fetcher(sample_url)

    if not detail_endpoint:
        log('\nERROR: No se pudo descubrir el endpoint de detalle.')
        log('Prueba abrir una página de producto en Chrome → DevTools → Network')
        log('y busca llamadas a api.modaverse.vip que devuelvan imageList.')
        log(f'Luego pasa el endpoint con --endpoint (o edita _DETAIL_ENDPOINTS en este script).')
        sys.exit(1)

    log(f'Usando endpoint: {detail_endpoint}')

    # ── Batch fetch ───────────────────────────────────────────────────────────
    url_to_idx = {p['url']: i for i, p in enumerate(products)}
    updated = failed = already_ok = 0

    log(f'\nDescargando detalles para {len(to_process)} productos...\n')

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        for i, p in enumerate(to_process, 1):
            pid = p.get('sku') or ''
            if not pid:
                failed += 1
                continue

            imgs = _fetch_detail(client, detail_endpoint, pid)

            if i % 50 == 0 or i <= 3:
                log(f'  [{i}/{len(to_process)}] {pid} → {len(imgs)} imgs')

            if imgs:
                idx = url_to_idx.get(p['url'])
                if idx is not None:
                    products[idx]['images'] = imgs
                    updated += 1
            else:
                failed += 1

            if args.delay > 0:
                time.sleep(args.delay)

    # ── Guardar JSON ──────────────────────────────────────────────────────────
    if updated:
        data['products'] = products
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(f'\nJSON guardado: {OUTPUT_PATH}')

    log(f'\n✅ Listo: {updated} productos actualizados, {failed} sin imágenes nuevas')
    log('Siguiente paso: python manage.py import_images (para descargar las imágenes a BD)')


if __name__ == '__main__':
    main()
