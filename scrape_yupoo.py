"""
Yupoo Shoe Factory Scraper
Scrapes https://putianshoefactory.x.yupoo.com/albums
- Lista de álbumes (modelos de tenis)
- Primeros 5 álbumes Nike/Adidas/Jordan con detalles
"""

import sys
import json
import time
import re
# Force UTF-8 output on Windows to handle Chinese characters
if sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from scrapling.fetchers import StealthyFetcher, StealthySession

OUTPUT_PATH = r"C:/Users/Lenovo/Documents/WEB_RYAL/scraped_yupoo.json"
BASE_URL = "https://putianshoefactory.x.yupoo.com"
ALBUMS_URL = f"{BASE_URL}/albums"

# Marcas objetivo
TARGET_BRANDS = ["nike", "adidas", "jordan", "aj", "air", "yeezy", "dunk", "force"]

FETCH_KWARGS = dict(
    network_idle=True,
    timeout=60000,
    wait=3000,
    headless=True,
    block_ads=True,
)


def is_target_brand(name: str) -> bool:
    name_lower = name.lower()
    return any(brand in name_lower for brand in TARGET_BRANDS)


def extract_price(text: str) -> tuple:
    """Extrae precio USD, CNY y MXN del texto."""
    price_usd = None
    price_cny = None
    price_mxn = None
    if not text:
        return price_usd, price_cny, price_mxn

    # MXN: "-360pesos", "- $360 MXN", "-360", etc. (patrón más específico primero)
    mxn_match = re.search(
        r'[-–]\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:pesos?|mxn)\b',
        text, re.IGNORECASE
    )
    if mxn_match:
        price_mxn = float(mxn_match.group(1))
    else:
        # Número suelto al final del nombre tipo "-360" sin unidad
        bare_match = re.search(r'[-–]\s*(\d{3,4})\s*$', text.strip())
        if bare_match:
            val = float(bare_match.group(1))
            if 100 <= val <= 5000:
                price_mxn = val

    # USD: $45, USD45
    usd_match = re.search(r'[\$]\s*(\d+(?:\.\d+)?)(?!\s*(?:pesos?|mxn))', text, re.IGNORECASE)
    if usd_match:
        price_usd = float(usd_match.group(1))

    # CNY: ¥45, CNY45, 45元
    cny_match = re.search(r'[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元', text, re.IGNORECASE)
    if cny_match:
        val = cny_match.group(1) or cny_match.group(2)
        if val:
            price_cny = float(val)

    return price_usd, price_cny, price_mxn


def _album_id_from_url(url: str) -> int | None:
    """Extrae el ID numérico del álbum desde la URL."""
    m = re.search(r'/albums/(\d+)', url)
    return int(m.group(1)) if m else None


def scrape_albums_page(session, url: str) -> list:
    """Extrae álbumes de una página de listado."""
    print(f"  Fetching albums page: {url}")
    try:
        page = session.fetch(url, **FETCH_KWARGS)
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}")
        return []

    albums = []

    # Yupoo structure: cada álbum está en un elemento con clase que contiene
    # el link, nombre e imagen de portada
    # Selectores comunes en Yupoo: .album-list .album, .album__main, etc.

    # Intentar varios selectores conocidos de Yupoo
    album_items = (
        page.css("div.album__main") or
        page.css("div.album") or
        page.css("li.album") or
        page.css(".album-list li") or
        page.css("[class*='album']")
    )

    print(f"  Found {len(album_items)} album elements")

    # Fallback: buscar todos los links con imágenes que parezcan álbumes
    if not album_items:
        print("  Trying fallback: anchor tags with images...")
        album_items = page.css("a[href*='/albums/']")
        print(f"  Fallback found {len(album_items)} elements")

    for item in album_items:
        try:
            # URL del álbum
            href = (
                item.attrib.get("href") or
                (item.css("a")[0].attrib.get("href") if item.css("a") else None)
            )
            if not href:
                continue

            album_url = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Nombre del álbum
            name = (
                (item.css("a.album__title::text").get() or "").strip() or
                (item.css(".album__title::text").get() or "").strip() or
                (item.css(".name::text").get() or "").strip() or
                (item.css("p::text").get() or "").strip() or
                (item.css("span::text").get() or "").strip() or
                item.get_all_text(strip=True)[:80]
            )

            # Thumbnail
            img_el = (
                item.css("img[src]") or
                item.css("img[data-src]") or
                item.css("img")
            )
            thumb = ""
            if img_el:
                thumb = (
                    img_el[0].attrib.get("src") or
                    img_el[0].attrib.get("data-src") or
                    img_el[0].attrib.get("data-original") or
                    ""
                )

            # Precio desde el nombre
            price_usd, price_cny, price_mxn = extract_price(name)
            album_id = _album_id_from_url(album_url)

            if album_url and (name or thumb):
                albums.append({
                    "name": name,
                    "url": album_url,
                    "thumbnail": thumb,
                    "album_id": album_id,
                    "price_usd": price_usd,
                    "price_cny": price_cny,
                    "price_mxn": price_mxn,
                })
        except Exception as e:
            print(f"  Error parsing album item: {e}")
            continue

    return albums


def get_next_page_url(page, current_url: str) -> str | None:
    """Detecta paginación y retorna URL de la siguiente página."""
    # Buscar link de "siguiente página"
    next_link = (
        page.css("a.pager__item--next") or
        page.css("a[rel='next']") or
        page.css(".pagination .next a") or
        page.css("a:contains('下一页')") or  # Chinese "next page"
        page.css("a:contains('Next')")
    )
    if next_link:
        href = next_link[0].attrib.get("href", "")
        if href:
            return href if href.startswith("http") else f"{BASE_URL}{href}"

    # Buscar parámetro page= en la URL
    return None


def scrape_product_detail(session, album: dict) -> dict:
    """Extrae detalles de un álbum/producto individual."""
    url = album["url"]
    print(f"  Fetching product: {album['name'][:50]}...")

    try:
        page = session.fetch(url, **FETCH_KWARGS)
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

    # Nombre completo
    full_name = (
        (page.css("h1::text").get() or "").strip() or
        (page.css(".album-detail__title::text").get() or "").strip() or
        (page.css(".title::text").get() or "").strip() or
        album["name"]
    )

    # Imágenes del producto (primeras 4)
    images = []
    img_selectors = [
        "div.album-photo img",
        ".photo img",
        ".photos img",
        ".gallery img",
        "img[data-src]",
        "img[src*='photo']",
    ]

    for sel in img_selectors:
        imgs = page.css(sel)
        if imgs:
            for img in imgs[:4]:
                src = (
                    img.attrib.get("src") or
                    img.attrib.get("data-src") or
                    img.attrib.get("data-original") or
                    ""
                )
                if src and src not in images and not src.endswith(".gif"):
                    images.append(src)
                if len(images) >= 4:
                    break
            if images:
                break

    # Si no encontramos con selectores específicos, buscar todas las imágenes grandes
    if not images:
        all_imgs = page.css("img")
        for img in all_imgs:
            src = img.attrib.get("src") or img.attrib.get("data-src") or ""
            # Filtrar thumbnails pequeños e iconos
            if src and "thumb" not in src.lower() and len(src) > 20:
                if src not in images:
                    images.append(src)
                if len(images) >= 4:
                    break

    # Descripción / texto de la página
    description = (
        (page.css(".album-detail__desc::text").get() or "").strip() or
        (page.css(".desc::text").get() or "").strip() or
        (page.css("p::text").get() or "").strip() or
        ""
    )

    # Precio desde descripción y nombre
    all_text = full_name + " " + description
    price_usd, price_cny, price_mxn = extract_price(all_text)
    if price_usd is None:
        price_usd = album.get("price_usd")
    if price_cny is None:
        price_cny = album.get("price_cny")
    if price_mxn is None:
        price_mxn = album.get("price_mxn")

    # Variantes — Yupoo rara vez muestra tallas/colores explícitamente
    # pero intentamos extraerlas del texto
    variants = {"color": [], "talla": []}

    # Buscar tallas en el texto
    size_pattern = re.findall(r"\b(3[5-9]|4[0-8])\b", all_text)
    if size_pattern:
        variants["talla"] = list(dict.fromkeys(size_pattern))  # deduplicar manteniendo orden

    # Buscar colores mencionados
    color_keywords = [
        "black", "white", "red", "blue", "green", "yellow", "grey", "gray",
        "pink", "purple", "orange", "brown", "beige", "navy",
        "negro", "blanco", "rojo", "azul", "verde", "amarillo", "gris",
        "黑", "白", "红", "蓝", "绿",
    ]
    found_colors = [c for c in color_keywords if c in all_text.lower()]
    if found_colors:
        variants["color"] = found_colors

    # SKU — intentar extraer del URL o nombre
    album_id = _album_id_from_url(url)

    return {
        "name": full_name,
        "album_id": album_id,
        "price_usd": price_usd,
        "price_cny": price_cny,
        "price_mxn": price_mxn,
        "images": images,
        "variants": variants,
        "description": description,
        "url": url,
        "thumbnail": album.get("thumbnail", ""),
    }


def main():
    print("=" * 60)
    print("Yupoo Shoe Factory Scraper")
    print("=" * 60)

    all_albums = []
    products = []

    with StealthySession(headless=True, block_ads=True, timeout=60000) as session:

        # ─── FASE 1: Recopilar álbumes (hasta 2 páginas) ───
        print("\n[1/2] Scraping albums list...")

        current_url = ALBUMS_URL
        pages_scraped = 0

        while current_url and pages_scraped < 2:
            pages_scraped += 1
            print(f"\n  Page {pages_scraped}: {current_url}")

            try:
                page = session.fetch(current_url, **FETCH_KWARGS)
            except Exception as e:
                print(f"  FATAL ERROR fetching page {pages_scraped}: {e}")
                break

            # Debug: mostrar título de la página
            title = (page.css("title::text").get() or "").strip()
            print(f"  Page title: {title}")

            # Extraer álbumes de esta página
            page_albums = []

            # --- Selector strategy 1: estructura típica Yupoo ---
            items = (
                page.css("div.album__main") or
                page.css("div.album-item") or
                page.css("li.album-item") or
                page.css(".album-list .album") or
                page.css(".albums .album")
            )

            if items:
                print(f"  Strategy 1 found {len(items)} items")
                for item in items:
                    try:
                        # Link
                        a_el = item.css("a") or [item] if item.tag == "a" else []
                        href = a_el[0].attrib.get("href", "") if a_el else ""
                        if not href:
                            href = item.attrib.get("href", "")

                        album_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                        # Nombre
                        name = (
                            (item.css(".album__title::text").get() or "").strip() or
                            (item.css(".title::text").get() or "").strip() or
                            (item.css("p::text").get() or "").strip() or
                            item.get_all_text(strip=True)[:100]
                        )

                        # Thumbnail
                        img = item.css("img")
                        thumb = ""
                        if img:
                            thumb = (
                                img[0].attrib.get("src") or
                                img[0].attrib.get("data-src") or
                                img[0].attrib.get("data-original") or ""
                            )

                        price_usd, price_cny, price_mxn = extract_price(name)
                        album_id = _album_id_from_url(album_url)

                        if href and name:
                            page_albums.append({
                                "name": name,
                                "url": album_url,
                                "thumbnail": thumb,
                                "album_id": album_id,
                                "price_usd": price_usd,
                                "price_cny": price_cny,
                                "price_mxn": price_mxn,
                            })
                    except Exception as e:
                        print(f"  Parse error: {e}")

            # --- Selector strategy 2: links directos a álbumes ---
            if not page_albums:
                print("  Strategy 2: links to /albums/ID...")
                links = page.css("a[href*='/albums/']")
                print(f"  Found {len(links)} album links")

                seen_hrefs = set()
                for link in links:
                    href = link.attrib.get("href", "")
                    if not href or href in seen_hrefs:
                        continue
                    # Evitar el link a la página de álbumes en sí
                    if href.rstrip("/") == "/albums" or href == ALBUMS_URL:
                        continue
                    seen_hrefs.add(href)

                    album_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                    # Texto e imagen dentro del link
                    name = link.get_all_text(strip=True)[:100]
                    img = link.css("img")
                    thumb = ""
                    if img:
                        thumb = (
                            img[0].attrib.get("src") or
                            img[0].attrib.get("data-src") or ""
                        )

                    # Si el link no tiene texto, buscar en el padre
                    if not name:
                        try:
                            name = link.parent.get_all_text(strip=True)[:100]
                        except Exception:
                            pass

                    price_usd, price_cny, price_mxn = extract_price(name)
                    album_id = _album_id_from_url(album_url)

                    page_albums.append({
                        "name": name or f"Album {len(page_albums)+1}",
                        "url": album_url,
                        "thumbnail": thumb,
                        "album_id": album_id,
                        "price_usd": price_usd,
                        "price_cny": price_cny,
                        "price_mxn": price_mxn,
                    })

            print(f"  Extracted {len(page_albums)} albums from page {pages_scraped}")
            all_albums.extend(page_albums)

            # Paginación
            next_url = None
            next_candidates = (
                page.css("a.pager__item--next") or
                page.css("a[rel='next']") or
                page.css(".next a") or
                page.css("a:contains('下一页')")
            )
            if next_candidates:
                next_href = next_candidates[0].attrib.get("href", "")
                if next_href:
                    next_url = next_href if next_href.startswith("http") else f"{BASE_URL}{next_href}"
                    print(f"  Next page found: {next_url}")

            current_url = next_url

            if current_url and pages_scraped < 2:
                time.sleep(2)  # pausa entre páginas

        print(f"\n  Total albums found: {len(all_albums)}")

        # ─── FASE 2: Detalles de los primeros 5 álbumes Nike/Adidas/Jordan ───
        print("\n[2/2] Scraping product details (Nike/Adidas/Jordan)...")

        # Filtrar por marca objetivo
        target_albums = [a for a in all_albums if is_target_brand(a["name"])]
        print(f"  Target brand albums: {len(target_albums)}")

        # Si no hay suficientes de las marcas objetivo, tomar los primeros disponibles
        if len(target_albums) < 5:
            print(f"  Not enough branded albums, adding non-branded ones to fill up to 5")
            non_target = [a for a in all_albums if not is_target_brand(a["name"])]
            target_albums = target_albums + non_target

        # Tomar los primeros 5
        albums_to_detail = target_albums[:5]
        print(f"  Will detail {len(albums_to_detail)} albums:")
        for i, a in enumerate(albums_to_detail, 1):
            print(f"    {i}. {a['name'][:60]}")

        for i, album in enumerate(albums_to_detail, 1):
            print(f"\n  [{i}/5] {album['name'][:50]}...")
            detail = scrape_product_detail(session, album)
            if detail:
                products.append(detail)
            time.sleep(2)  # pausa entre productos

    # ─── SALIDA ───
    output = {
        "source": "yupoo",
        "base_url": BASE_URL,
        "albums_url": ALBUMS_URL,
        "albums_total": len(all_albums),
        "albums_list": all_albums,  # todos los álbumes encontrados
        "products": products,       # detalles de los 5 seleccionados
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"DONE.")
    print(f"Albums found:    {len(all_albums)}")
    print(f"Products detail: {len(products)}")
    print(f"Output saved to: {OUTPUT_PATH}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
