"""
Re-scrapea páginas de listado de yupoo_pf para extraer precios correctos
y actualiza scraped_yupoo_pf.json + BD vía manage.py.

No entra a cada álbum (rápido ~5-10 min). Solo actualiza price_mxn.

Uso:
    python fix_calzado_prices.py              # actualiza JSON
    python fix_calzado_prices.py --dry-run    # muestra qué cambiaría
"""
import argparse
import json
import re
import time
from pathlib import Path

from scrapling.fetchers import StealthySession

# Misma fuente que el scraper principal
BASE_URL    = 'https://putianshoefactory.x.yupoo.com'
OUTPUT_PATH = Path(__file__).parent / 'scraped_yupoo_pf.json'

BRAND_CATEGORIES = {
    '4648664': 'Nike',          '4648665': 'Air Jordan',
    '4648690': 'Adidas',        '4649788': 'New Balance',
    '4648470': 'On Running',    '4649524': 'Hoka',
    '4800018': 'Asics',         '4653323': 'Brooks',
    '4916439': 'Vans',          '4916440': 'Converse',
    '4653498': 'Yeezy',         '4652708': 'Bape',
    '4651374': 'Louis Vuitton', '5081627': 'Balenciaga',
    '4812119': 'Alexander McQueen', '5081629': 'Dior',
    '5081661': 'Valentino',     '5109747': 'GGDB',
    '4883947': 'Timberland',    '5081638': 'Armani',
    '5081653': 'Lacoste',       '5081637': 'Boss',
    '5153305': 'The North Face','5081658': 'UGG',
    '5081666': 'Reebok',        '4653503': 'Amiri',
}

BRAND_DEFAULT_PRICE = {
    'Nike': 500.0, 'Air Jordan': 550.0, 'Adidas': 500.0,
    'New Balance': 480.0, 'On Running': 550.0, 'Hoka': 500.0,
    'Asics': 450.0, 'Brooks': 450.0, 'Vans': 380.0, 'Converse': 380.0,
    'Yeezy': 650.0, 'Bape': 700.0, 'Louis Vuitton': 950.0,
    'Balenciaga': 900.0, 'Alexander McQueen': 850.0, 'Dior': 950.0,
    'Valentino': 900.0, 'GGDB': 750.0, 'Timberland': 450.0,
    'Armani': 780.0, 'Lacoste': 500.0, 'Boss': 560.0,
    'The North Face': 480.0, 'UGG': 560.0, 'Reebok': 420.0, 'Amiri': 780.0,
}

FETCH_KWARGS = dict(network_idle=True, timeout=60_000, wait=2_000,
                    headless=True, block_ads=True)


def extract_price(text: str) -> float | None:
    if not text:
        return None
    m = re.search(r'[-–]?\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:pesos?|mxn)\b', text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # "- $300" o "- 300"
    m = re.search(r'[-–]\s*\$?\s*(\d{3,4})\s*$', text.strip())
    if m:
        val = float(m.group(1))
        if 100 <= val <= 5_000:
            return val
    return None


def album_id_from_url(url: str) -> str | None:
    m = re.search(r'/albums/(\d+)', url)
    return m.group(1) if m else None


def scrape_listing_prices(session, cat_id: str) -> dict[str, float]:
    """Scrapea todas las páginas de listado de una marca. Retorna {album_id: price}."""
    prices = {}
    page = 1
    while True:
        url = f'{BASE_URL}/categories/{cat_id}?page={page}'
        try:
            resp = session.fetch(url, **FETCH_KWARGS)
        except Exception as e:
            print(f'    ERROR página {page}: {e}')
            break

        found = 0
        for link in resp.css('a.album__main'):
            href  = link.attrib.get('href', '')
            title = link.attrib.get('title', '').strip()
            aid   = album_id_from_url(href)
            if aid and title:
                price = extract_price(title)
                if price:
                    prices[aid] = price
                    found += 1

        has_next = bool(
            resp.css('a.pager__item--next') or
            resp.css('a[rel="next"]') or
            resp.css("a:contains('下一页')")
        )
        print(f'    página {page}: {found} precios extraídos')
        if not has_next:
            break
        page += 1
        time.sleep(1.5)

    return prices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not OUTPUT_PATH.exists():
        print(f'ERROR: no se encontró {OUTPUT_PATH}')
        return

    with open(OUTPUT_PATH, encoding='utf-8') as f:
        data = json.load(f)

    products = data['products']
    print(f'JSON cargado: {len(products)} productos\n')

    # Índice album_id → producto
    aid_to_idx: dict[str, int] = {}
    for i, p in enumerate(products):
        aid = album_id_from_url(p.get('url', ''))
        if aid:
            aid_to_idx[aid] = i

    updated = unchanged = no_price = 0
    changes: list[tuple[str, float, float]] = []  # (name, old, new)

    with StealthySession(headless=True, block_ads=True) as session:
        for cat_id, brand_name in BRAND_CATEGORIES.items():
            print(f'[{brand_name}]')
            brand_default = BRAND_DEFAULT_PRICE.get(brand_name, 500.0)
            listing_prices = scrape_listing_prices(session, cat_id)

            for aid, price in listing_prices.items():
                idx = aid_to_idx.get(aid)
                if idx is None:
                    continue
                old = products[idx].get('price_mxn', brand_default)
                if abs(price - old) > 0.01:
                    changes.append((products[idx]['name'], old, price))
                    if not args.dry_run:
                        products[idx]['price_mxn'] = price
                    updated += 1
                else:
                    unchanged += 1

            # Productos sin precio en listado → mantener brand_default
            time.sleep(2)

    print(f'\nResumen: {updated} actualizados · {unchanged} sin cambio')
    if args.dry_run:
        print('\nDRY RUN — cambios que se aplicarían:')
        for name, old, new in changes[:20]:
            print(f'  {name[:40]:40s}  {old:.0f} → {new:.0f}')
        return

    if updated:
        data['products'] = products
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'\nJSON guardado: {OUTPUT_PATH}')
        print('\nSiguiente paso — actualizar BD en servidor:')
        print('  scp scraped_yupoo_pf.json root@5.161.249.245:/root/app/scraped_yupoo_pf.json')
        print('  Luego en servidor: python manage.py update_calzado_prices')


if __name__ == '__main__':
    main()
