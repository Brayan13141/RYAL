"""
Scraper para modaverse.vip — usa DynamicFetcher para capturar el árbol de categorías
y httpx POST para obtener todos los productos con su subcategoryId correcto.

Estrategia de scraping:
  1. Scrape plano (sin categoryId) — captura lo que devuelve la paginación global
  2. Scrape por cada categoría/subcategoría del árbol — garantiza cobertura completa
  Los resultados se deducan por productId.
"""

import json
import re
import io
import sys
import time
from collections import Counter
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import httpx
from scrapling.fetchers import DynamicFetcher


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _normalize_image_url(raw: str) -> str:
    """Convierte filename parcial a URL completa usando el timestamp de 13 dígitos."""
    if not raw:
        return raw
    if raw.startswith('http'):
        return raw
    match = re.search(r'(\d{13})', raw)
    if not match:
        return raw
    ts_ms    = int(match.group(1))
    date_str = datetime.utcfromtimestamp(ts_ms / 1000).strftime('%Y%m%d')
    return f"https://api.modaverse.vip/kkd_boot/file/static/{date_str}/{raw}"


def parse_float(val):
    if val is None:
        return 0.0
    try:
        return float(val)
    except Exception:
        cleaned = re.sub(r"[^\d.]", "", str(val))
        try:
            return float(cleaned) if cleaned else 0.0
        except Exception:
            return 0.0


# ─── Constantes ───────────────────────────────────────────────────────────────

OUTPUT_PATH = r"C:/Users/Lenovo/Documents/WEB_RYAL/scraped_modaverse.json"
INDEX_URL   = "https://www.modaverse.vip/#/index/US20260121113948017529"
API_BASE    = "https://api.modaverse.vip/kkd_boot"
PAGE_SIZE   = 100
MAX_RETRIES = 3

HEADERS = {
    'Content-Type':    'application/json',
    'Referer':         'https://www.modaverse.vip/',
    'Origin':          'https://www.modaverse.vip',
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'es-MX,es;q=0.9',
    'User-Agent':      ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/124.0.0.0 Safari/537.36'),
}

results = {
    "source":    "modaverse.vip",
    "currency":  "MXN",
    "categories": [],
    "products":  [],
    "debug":     [],
}


def log(msg):
    try:
        print(f"[scraper] {msg}", flush=True)
    except Exception:
        pass
    results["debug"].append(str(msg))


# ─── PASO 1: Árbol de categorías ──────────────────────────────────────────────
log("Fetching category tree from DynamicFetcher XHR...")
page = DynamicFetcher.fetch(
    INDEX_URL,
    network_idle=True,
    wait=6000,
    timeout=90000,
    capture_xhr=r".*getAllCategoryList.*",
    headless=True,
)
log(f"Status={page.status}, XHR captured={len(page.captured_xhr)}")

categories_raw = []
for xhr in page.captured_xhr:
    url  = getattr(xhr, 'url', '') or ''
    body = getattr(xhr, 'body', b'') or b''
    if 'getAllCategoryList' not in url or not body:
        continue
    try:
        data = json.loads(body.decode('utf-8'))
        categories_raw = data.get('data', [])
        log(f"  Categorías cargadas via XHR: {len(categories_raw)}")
    except Exception as e:
        log(f"  Error parseando XHR: {e}")

if not categories_raw:
    log("  Fallback: GET directo a getAllCategoryList...")
    try:
        r = httpx.get(f"{API_BASE}/category/getAllCategoryList", headers=HEADERS, timeout=30)
        categories_raw = r.json().get('data', [])
        log(f"  Fallback: {len(categories_raw)} categorías")
    except Exception as e:
        log(f"  Fallback error: {e}")


# ─── PASO 2: Parsear árbol ────────────────────────────────────────────────────
cat_id_to_name   = {}
cat_id_to_parent = {}

for cat in categories_raw:
    cid   = cat.get('categoryId', '')
    name  = cat.get('foreignLanguageName') or cat.get('categoryName') or ''
    cat_id_to_name[cid] = name

    subcats = []
    for sub in (cat.get('categoryList') or []):
        sid   = sub.get('categoryId', '')
        sname = sub.get('foreignLanguageName') or sub.get('categoryName') or ''
        cat_id_to_name[sid]    = sname
        cat_id_to_parent[sid]  = cid
        subcats.append({'id': sid, 'name_zh': sub.get('categoryName', ''), 'name_es': sname})

    results['categories'].append({
        'id':            cid,
        'name_zh':       cat.get('categoryName', ''),
        'name_es':       name,
        'subcategories': subcats,
    })

log(f"Total categorías mapeadas: {len(cat_id_to_name)}")


# ─── PASO 3: Scraping de productos ───────────────────────────────────────────

def _fetch_page_range(client, extra_payload=None, label="global", verbose=True):
    """
    Pagina el endpoint getUserPage. Acepta payload extra (e.g. {"categoryId": cid}).
    Reintentos con backoff exponencial. Retorna lista de records.
    """
    out = []
    pn  = 1
    while True:
        payload = {"page": pn, "size": PAGE_SIZE}
        if extra_payload:
            payload.update(extra_payload)

        data = None
        for attempt in range(MAX_RETRIES):
            try:
                r    = client.post(f"{API_BASE}/product/getUserPage", json=payload, timeout=30)
                data = r.json()
                break
            except Exception as e:
                wait = 2 ** attempt
                if attempt < MAX_RETRIES - 1:
                    log(f"  [{label}] p{pn} intento {attempt+1} falló ({e}) — reintentando en {wait}s")
                    time.sleep(wait)
                else:
                    log(f"  [{label}] p{pn}: error permanente — {e}")
                    return out

        if data is None or not data.get('success'):
            msg = data.get('message', '') if data else 'sin respuesta'
            log(f"  [{label}] p{pn}: API error — {msg}")
            break

        records     = data.get('data', {}).get('records', []) or []
        total_pages = data.get('data', {}).get('pages', 1)
        out.extend(records)

        if verbose:
            log(f"  [{label}] p{pn}/{total_pages}: {len(records)} prods")

        if pn >= total_pages or not records:
            break
        pn += 1
        time.sleep(0.3)

    return out


all_products_raw = []
seen_ids         = set()

log("\n── Fase 1: Scrape plano (sin filtro de categoría) ──")
with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
    flat = _fetch_page_range(client, label="global")
    for p in flat:
        pid = p.get('productId')
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            all_products_raw.append(p)

log(f"Fase 1 completa: {len(all_products_raw)} productos únicos")

# ── Fase 2: Scrape por cada categoría/subcategoría ────────────────────────────
log(f"\n── Fase 2: Scrape por {len(cat_id_to_name)} categorías/subcategorías ──")

all_cat_ids  = list(cat_id_to_name.keys())
new_from_cat = 0

with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
    for i, cid in enumerate(all_cat_ids, 1):
        cname    = cat_id_to_name.get(cid, cid)
        cat_prods = _fetch_page_range(
            client,
            extra_payload={"categoryId": cid},
            label=f"{i}/{len(all_cat_ids)} {cname[:18]}",
            verbose=False,   # silencioso por página, solo resumen al final
        )

        added = 0
        for p in cat_prods:
            pid = p.get('productId')
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                all_products_raw.append(p)
                added += 1

        if cat_prods or added:
            log(f"  [{i}/{len(all_cat_ids)}] {cname}: {len(cat_prods)} prods, {added} nuevos")
        new_from_cat += added

log(f"\nFase 2 completa: {new_from_cat} productos nuevos encontrados por categoría")
log(f"Total después de ambas fases: {len(all_products_raw)}")


# ─── PASO 4: Fallback XHR si ambas fases devuelven 0 ─────────────────────────
if not all_products_raw:
    log("\nAmbas fases vacías — fallback DynamicFetcher XHR...")
    prod_page = DynamicFetcher.fetch(
        "https://www.modaverse.vip/#/product/CA20260107160742000002",
        network_idle=True, wait=6000, timeout=90000,
        capture_xhr=r".*getUserPage.*", headless=True,
    )
    for xhr in prod_page.captured_xhr:
        url  = getattr(xhr, 'url', '') or ''
        body = getattr(xhr, 'body', b'') or b''
        if 'getUserPage' not in url or not body:
            continue
        try:
            d    = json.loads(body.decode('utf-8'))
            recs = d.get('data', {}).get('records', []) if d.get('data') else []
            for p in recs:
                pid = p.get('productId')
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    all_products_raw.append(p)
            log(f"  XHR fallback: {len(recs)} prods")
        except Exception as e:
            log(f"  XHR parse error: {e}")


# ─── PASO 5: Mapear al esquema de salida ─────────────────────────────────────
log(f"\nMapeando {len(all_products_raw)} productos...")

all_mapped = []
for item in all_products_raw:
    pid      = item.get('productId', '')
    cat_id   = item.get('categoryId', '')
    cat_name = cat_id_to_name.get(cat_id, 'General')

    # Imágenes
    img_raw = item.get('imageList', '') or ''
    if isinstance(img_raw, list):
        images = [_normalize_image_url(u.strip()) for u in img_raw if u and u.strip()]
    elif isinstance(img_raw, str) and img_raw:
        images = [_normalize_image_url(u.strip()) for u in re.split(r'[,\r\n]+', img_raw) if u.strip()]
    else:
        images = []
    images = [u for u in images if u and u.startswith('http')]

    price_mxn = parse_float(item.get('unitPrice'))
    stock_num = item.get('stockNum', 0)

    if stock_num is not None and int(stock_num or 0) <= 0:
        status = 'out_of_stock'
    elif item.get('ynLaunch') == '1':
        status = 'available'
    else:
        status = 'unlaunched'

    all_mapped.append({
        "name":         item.get('productName', ''),
        "sku":          pid,
        "product_code": item.get('productCode', ''),
        "price_mxn":    price_mxn,
        "price_usd":    parse_float(item.get('dollarUnitPrice')),
        "currency":     "MXN",
        "images":       images,
        "variants":     {},
        "description":  "",
        "status":       status,
        "stock":        stock_num,
        "tags":         [],
        "category_id":  cat_id,
        "category":     cat_name,
        "url":          (
            f"https://www.modaverse.vip/#/product/{cat_id}?pid={pid}"
            if cat_id else
            f"https://www.modaverse.vip/#/product/{pid}"
        ),
    })

log(f"Productos mapeados: {len(all_mapped)}")

unknown_cat_ids = set(
    p['category_id'] for p in all_mapped
    if p['category_id'] and p['category_id'] not in cat_id_to_name
)
if unknown_cat_ids:
    log(f"Categorías no encontradas en árbol: {len(unknown_cat_ids)} IDs")
    results['unknown_category_ids'] = list(unknown_cat_ids)


# ─── PASO 6: Guardar JSON ─────────────────────────────────────────────────────
results["products"]       = all_mapped
results["total_products"] = len(all_mapped)

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

log(f"\nJSON escrito: {OUTPUT_PATH}")


# ─── RESUMEN ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("RESULTADO FINAL — modaverse.vip")
print("=" * 65)
print(f"  Categorías padre: {len(results['categories'])}")
for c in results['categories']:
    nsubs = len(c['subcategories'])
    print(f"    - {c['name_es']} ({nsubs} subcats)")

print(f"\n  Fase 1 (plano): {len(flat)} productos")
print(f"  Fase 2 (por cat): {new_from_cat} productos nuevos")
print(f"  Total único:    {len(all_mapped)}")

dist = Counter()
for p in all_mapped:
    cid        = p['category_id']
    parent_id  = cat_id_to_parent.get(cid, cid)
    parent_name = cat_id_to_name.get(parent_id, cat_id_to_name.get(cid, '?'))
    dist[parent_name] += 1

print("\n  Distribución por categoría padre:")
for name, count in dist.most_common():
    print(f"    {name}: {count}")

bad = sum(1 for p in all_mapped for img in p['images'] if not img.startswith('http'))
print(f"\n  URLs de imagen inválidas restantes: {bad}")
if unknown_cat_ids:
    print(f"  Categorías sin mapear: {len(unknown_cat_ids)}")
print(f"\n  JSON: {OUTPUT_PATH}")
print("=" * 65)
