"""
Yupoo PF Scraper — Calzado por marcas
Fuente: https://putianshoefactory.x.yupoo.com

Produce scraped_yupoo_pf.json compatible con load_productos.py

Uso:
  python scrape_yupoo_pf.py                        # todas las marcas
  python scrape_yupoo_pf.py --brands Nike Adidas    # marcas específicas
  python scrape_yupoo_pf.py --limit 30              # máx 30 álbumes por marca
  python scrape_yupoo_pf.py --with-detail           # entra a cada álbum para N imágenes
  python scrape_yupoo_pf.py --resume                # continúa desde JSON existente
"""
import sys
import os

if sys.stdout.encoding.lower() != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import json
import re
import time
import argparse
from datetime import datetime
from pathlib import Path

from scrapling.fetchers import StealthySession

# ── Configuración ──────────────────────────────────────────────────────────────

BASE_URL    = 'https://putianshoefactory.x.yupoo.com'
OUTPUT_PATH = Path(__file__).parent / 'scraped_yupoo_pf.json'

# Nombre visible para la categoría padre (no revela el proveedor)
PARENT_CATEGORY_NAME = 'Calzado'
PARENT_CATEGORY_ID   = 'yupoo_pf_root'

# Precio base por defecto por marca (MXN) — aplica cuando el título no trae precio
BRAND_DEFAULT_PRICE: dict[str, float] = {
    'Nike':             500.0,
    'Air Jordan':       550.0,
    'Adidas':           500.0,
    'New Balance':      480.0,
    'On Running':       550.0,
    'Hoka':             500.0,
    'Asics':            450.0,
    'Brooks':           450.0,
    'Vans':             380.0,
    'Converse':         380.0,
    'Yeezy':            650.0,
    'Bape':             700.0,
    'Louis Vuitton':    950.0,
    'Balenciaga':       900.0,
    'Alexander McQueen':850.0,
    'Dior':             950.0,
    'Valentino':        900.0,
    'GGDB':             750.0,
    'Timberland':       450.0,
    'Armani':           780.0,
    'Lacoste':          500.0,
    'Boss':             560.0,
    'The North Face':   480.0,
    'UGG':              560.0,
    'Reebok':           420.0,
    'Amiri':            780.0,
}

# Marcas → {category_id: display_name}
# IDs descubiertos del sidebar en albums?tab=gallery&page=1
BRAND_CATEGORIES: dict[str, str] = {
    '4648664': 'Nike',
    '4648665': 'Air Jordan',
    '4648690': 'Adidas',
    '4649788': 'New Balance',
    '4648470': 'On Running',
    '4649524': 'Hoka',
    '4800018': 'Asics',
    '4653323': 'Brooks',
    '4916439': 'Vans',
    '4916440': 'Converse',
    '4653498': 'Yeezy',
    '4652708': 'Bape',
    '4651374': 'Louis Vuitton',
    '5081627': 'Balenciaga',
    '4812119': 'Alexander McQueen',
    '5081629': 'Dior',
    '5081661': 'Valentino',
    '5109747': 'GGDB',
    '4883947': 'Timberland',
    '5081638': 'Armani',
    '5081653': 'Lacoste',
    '5081637': 'Boss',
    '5153305': 'The North Face',
    '5081658': 'UGG',
    '5081666': 'Reebok',
    '4653503': 'Amiri',
}

FETCH_KWARGS = dict(
    network_idle=True,
    timeout=60_000,
    wait=2_000,
    headless=True,
    block_ads=True,
)

MAX_RETRIES = 3
RETRY_DELAYS = [3, 7, 15]  # segundos entre reintentos


# ── Utilidades ─────────────────────────────────────────────────────────────────

def extract_price(text: str) -> float | None:
    if not text:
        return None
    # "500pesos", "- $500 MXN", "- 500 pesos"
    m = re.search(r'[-–]?\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:pesos?|mxn)\b', text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Número suelto al final: "- 500" o "- $500"
    m = re.search(r'[-–]\s*\$?\s*(\d{3,4})\s*$', text.strip())
    if m:
        val = float(m.group(1))
        if 100 <= val <= 5_000:
            return val
    return None


def clean_name(raw: str) -> str:
    """Quita precio y caracteres chinos del título del álbum."""
    name = re.sub(r'\s*[-–]\s*\$?\d+(?:\.\d+)?\s*(?:pesos?|mxn|usd|cny)?', '', raw, flags=re.IGNORECASE)
    name = re.sub(r'[一-鿿㐀-䶿]+', '', name)
    return name.strip(' .-_') or raw


def album_id_from_url(url: str) -> str | None:
    m = re.search(r'/albums/(\d+)', url)
    return m.group(1) if m else None


def clean_album_url(href: str) -> str:
    """Convierte href relativo a URL canónica; conserva uid (requerido por yupoo)."""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    if not href.startswith('http'):
        href = BASE_URL + href
    parsed = urlparse(href)
    params = parse_qs(parsed.query, keep_blank_values=False)
    # uid identifica al seller — sin él yupoo devuelve 404 en la página del álbum
    uid_vals = params.get('uid', [])
    new_query = urlencode({'uid': uid_vals[0]}) if uid_vals else ''
    return urlunparse(parsed._replace(query=new_query))


def upgrade_img_url(url: str) -> str:
    """Convierte thumbnails 'small' a 'medium' para mejor calidad."""
    return re.sub(r'/small\.', '/medium.', url)


def fetch_with_retry(session, url: str, retries: int = MAX_RETRIES) -> object | None:
    for attempt in range(retries):
        try:
            return session.fetch(url, **FETCH_KWARGS)
        except Exception as e:
            if attempt < retries - 1:
                delay = RETRY_DELAYS[attempt]
                print(f'    Retry {attempt + 1}/{retries - 1} en {delay}s ({e})')
                time.sleep(delay)
            else:
                print(f'    ERROR tras {retries} intentos: {e}')
                return None


# ── Scraping ───────────────────────────────────────────────────────────────────

def scrape_category_page(session, category_id: str, page: int) -> tuple[list[dict], bool]:
    """
    Scrape una página de listado de álbumes de una categoría.
    Retorna (lista_albumes, hay_siguiente_pagina).
    """
    url = f'{BASE_URL}/categories/{category_id}?page={page}'
    print(f'    [{page}] {url}')

    response = fetch_with_retry(session, url)
    if response is None:
        return [], False

    albums = []

    for link in response.css('a.album__main'):
        href  = link.attrib.get('href', '')
        title = link.attrib.get('title', '').strip()
        if not href or not title:
            continue

        album_url = clean_album_url(href)
        album_id  = album_id_from_url(album_url)
        if not album_id:
            continue

        # Thumbnail — preferir src sobre data-src
        img = link.css('img.album__img')
        thumb = ''
        if img:
            src = img[0].attrib.get('src') or img[0].attrib.get('data-src') or ''
            thumb = upgrade_img_url(src)

        albums.append({
            'album_id': album_id,
            'name':     title,
            'thumbnail': thumb,
            'price_mxn': extract_price(title),
            'url':       album_url,
        })

    # Paginación
    has_next = bool(
        response.css('a.pager__item--next') or
        response.css('a[rel="next"]') or
        response.css("a:contains('下一页')")
    )

    print(f'    → {len(albums)} álbumes, siguiente={has_next}')
    return albums, has_next


def scrape_album_detail(session, album: dict) -> tuple[list[str], float | None]:
    """
    Abre la página de un álbum y extrae imágenes + precio.
    Retorna (images, price_mxn | None).
    Fallback: thumbnail de listado si falla.
    """
    url = album['url']
    response = fetch_with_retry(session, url)
    if response is None:
        return ([album['thumbnail']] if album.get('thumbnail') else []), None

    images = []

    # Yupoo renderiza las fotos con class 'album-photo' o dentro de divs .photo
    for sel in [
        'div.album-photo img',
        '.photo-content img',
        '.photos img',
        'img[src*="photo.yupoo.com"]',
        'img[data-src*="photo.yupoo.com"]',
    ]:
        for img in response.css(sel):
            src = (
                img.attrib.get('src') or
                img.attrib.get('data-src') or
                img.attrib.get('data-original') or ''
            )
            src = upgrade_img_url(src)
            if src and 'photo.yupoo.com' in src and src not in images:
                images.append(src)
        if images:
            break

    if not images and album.get('thumbnail'):
        images = [album['thumbnail']]

    # Intentar extraer precio del cuerpo de la página
    page_price = None
    for sel in ['.album-intro', '.intro', '.description', 'p', 'span', 'div.text']:
        for el in response.css(sel):
            text = el.attrib.get('text', '') or ''
            if not text:
                # scrapling: el texto está en .text o en el nodo
                try:
                    text = str(el.text) or ''
                except Exception:
                    text = ''
            p = extract_price(text)
            if p:
                page_price = p
                break
        if page_price:
            break

    return images[:250], page_price


# ── Main ───────────────────────────────────────────────────────────────────────

def save_json(categories: list, products: list) -> None:
    data = {
        'source':     'yupoo_pf',
        'scraped_at': datetime.now().isoformat(),
        'base_url':   BASE_URL,
        'categories': categories,
        'products':   products,
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description='Yupoo PF Scraper — Calzado por marcas')
    parser.add_argument('--brands',      nargs='+', metavar='MARCA',
                        help='Solo estas marcas (nombre o ID). Ej: Nike "Air Jordan"')
    parser.add_argument('--limit',       type=int, default=0,
                        help='Máximo de álbumes por marca (0 = sin límite)')
    parser.add_argument('--with-detail', action='store_true',
                        help='Entra a cada álbum para obtener todas las fotos (lento)')
    parser.add_argument('--resume',      action='store_true',
                        help='Salta URLs ya presentes en scraped_yupoo_pf.json')
    args = parser.parse_args()

    print('=' * 60)
    print('Yupoo PF — Calzado por marcas')
    print(f'  with-detail : {args.with_detail}')
    print(f'  limit       : {args.limit or "sin límite"}')
    print(f'  resume      : {args.resume}')
    print('=' * 60)

    # ── Cargar productos existentes si se resume ──────────────────────────────
    existing_urls: set[str] = set()
    existing_products: list[dict] = []
    if args.resume and OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding='utf-8') as f:
            prev = json.load(f)
        existing_products = prev.get('products', [])
        existing_urls = {p['url'] for p in existing_products}
        print(f'Resume: {len(existing_products)} productos ya guardados\n')

    # ── Filtrar marcas ────────────────────────────────────────────────────────
    if args.brands:
        args_lower = [b.lower() for b in args.brands]
        brands = {
            k: v for k, v in BRAND_CATEGORIES.items()
            if k in args.brands or v.lower() in args_lower
        }
        if not brands:
            print(f'ERROR: ninguna marca coincide con {args.brands}')
            print(f'Marcas disponibles: {", ".join(BRAND_CATEGORIES.values())}')
            return
    else:
        brands = BRAND_CATEGORIES

    # ── Árbol de categorías para el JSON ─────────────────────────────────────
    categories_tree = [
        {
            'id':       PARENT_CATEGORY_ID,
            'name_es':  PARENT_CATEGORY_NAME,
            'name_zh':  PARENT_CATEGORY_NAME,
            'subcategories': [
                {'id': f'yupoo_pf_{cid}', 'name_es': name, 'name_zh': name}
                for cid, name in brands.items()
            ],
        }
    ]

    all_products: list[dict] = list(existing_products)

    with StealthySession(headless=True, block_ads=True) as session:

        for cat_id, brand_name in brands.items():
            sub_id = f'yupoo_pf_{cat_id}'
            print(f'\n[{brand_name}]  category_id={cat_id}')

            # ── Recopilar álbumes de todas las páginas ────────────────────────
            brand_albums: list[dict] = []
            page = 1

            while True:
                albums, has_next = scrape_category_page(session, cat_id, page)
                brand_albums.extend(albums)

                if args.limit and len(brand_albums) >= args.limit:
                    brand_albums = brand_albums[:args.limit]
                    break

                if not has_next or not albums:
                    break

                page += 1
                time.sleep(1.5)

            total = len(brand_albums)
            skipped = sum(1 for a in brand_albums if a['url'] in existing_urls)
            print(f'  {total} álbumes  ({skipped} ya guardados)')

            brand_default = BRAND_DEFAULT_PRICE.get(brand_name, 500.0)

            # ── Procesar cada álbum ───────────────────────────────────────────
            for i, album in enumerate(brand_albums, 1):
                url = album['url']

                if url in existing_urls:
                    continue

                name = clean_name(album['name']) or brand_name

                # Precio: título > detalle de página > default por marca
                price = album.get('price_mxn')

                if args.with_detail:
                    print(f'  [{i}/{total}] {name[:50]}...')
                    images, detail_price = scrape_album_detail(session, album)
                    if not price and detail_price:
                        price = detail_price
                    time.sleep(1.2)
                else:
                    images = [album['thumbnail']] if album.get('thumbnail') else []

                if not price:
                    price = brand_default

                product = {
                    'name':        name,
                    'category_id': sub_id,
                    'category':    brand_name,
                    'price_mxn':   price,
                    'images':      images,
                    'url':         url,
                    'description': f'Tenis {brand_name}.',
                    'album_id':    album['album_id'],
                }
                all_products.append(product)
                existing_urls.add(url)

            # Guardar avance tras cada marca
            save_json(categories_tree, all_products)
            print(f'  ✓ Guardado ({len(all_products)} productos en total)')
            time.sleep(2)

    save_json(categories_tree, all_products)
    print(f'\n{"="*60}')
    print(f'DONE. {len(all_products)} productos → {OUTPUT_PATH.name}')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()
