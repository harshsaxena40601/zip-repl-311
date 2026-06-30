import json
import re
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import requests
try:
    from curl_cffi import requests as cffi_requests
    _HAS_CFFI = True
except ImportError:
    _HAS_CFFI = False

from core.tag_engine import (
    clean_title, generate_handle, apply_standardized_tags,
    detect_gender, sanitize_html_description,
    build_full_tags, append_brand_message, build_mirage_description,
)
from core.db import (
    upsert_all_product_data, start_scrape_record, update_scrape_record,
    heartbeat_scrape_record, upload_csv_to_supabase,
)
from core.shopify_transformer import transform_to_shopify, export_shopify_csv

BASE_URL   = "https://www.mytheresa.com"
SCRAPER_ID = "mytheresa"
CURRENCY   = "EUR"

_MYTHERESA_TYPE_SINGULAR = {
    "Minidresses": "minidress", "Gowns": "gown", "Shirts": "shirt",
    "Sweaters": "sweater", "Miniskirts": "miniskirt", "Blazers": "blazer",
    "Blouses": "blouse", "Loafers": "loafer", "Cardigans": "cardigan",
    "Slides": "slide", "Mules": "mule", "Swimsuits": "swimsuit",
    "Earrings": "earring", "Bikinis": "bikini", "Shorts": "pair of shorts",
    "Bodysuits": "bodysuit", "Scarves": "scarf", "Vests": "vest",
    "Gloves": "glove", "Sunglasses": "sunglasses piece", "Belts": "belt",
    "Leggings": "pair of leggings", "Kaftans": "kaftan", "Chinos": "chino",
    "Shoppers": "shopper", "Sweatshirts": "sweatshirt", "Bombers": "bomber",
    "Bracelets": "bracelet", "Necklaces": "necklace", "Hoodies": "hoodie",
    "Clutches": "clutch", "Beanies": "beanie", "Sandals": "sandal",
    "Sweatpants": "pair of sweatpants", "Pouches": "pouch", "Rings": "ring",
    "Wallets": "wallet", "Caps": "cap", "Bras": "bra", "Parkas": "parka",
    "Socks": "pair of socks", "Business": "business piece",
    "Mini Dresses": "mini dress", "Maxi Dresses": "maxi dress",
    "Mini Skirts": "mini skirt", "Maxi Skirts": "maxi skirt",
    "Square Sunglasses": "square sunglasses", "Round Sunglasses": "round sunglasses",
    "Cat Eye Sunglasses": "cat-eye sunglasses",
    "Platform Sandals": "platform sandal", "Platform Boots": "platform boot",
    "Ankle Boots": "ankle boot", "Chelsea Boots": "Chelsea boot",
    "Knee Boots": "knee boot", "Combat Boots": "combat boot",
    "Lace-Up Shoes": "lace-up shoe", "Slip-On Shoes": "slip-on shoe",
}

MYTHERESA_API = "https://api.mytheresa.com/api"

GRAPHQL_QUERY = """
query XProductListingPageQuery(
    $designers: [String], $page: Int, $size: Int, $slug: String, $sort: String
) {
    xProductListingPage(
        designers: $designers, page: $page, size: $size, slug: $slug, sort: $sort
    ) {
        pagination { totalItems totalPages }
        products {
            color
            combinedCategoryName
            description
            designer
            designerInfo { displayName }
            displayImages
            hasStock
            isPurchasable
            mainWaregroup
            name
            slug
            variants {
                availability { hasStock }
                price { discount original }
                size
                sku
            }
        }
    }
}
"""

# ── Per-gender designer lists (from user-supplied sale URLs) ──────────────────
WOMEN_DESIGNERS = [
    "Alaïa", "Ami Paris", "Amina Muaddi", "Amiri", "Aquazzura",
    "Balenciaga", "Balmain", "Burberry", "Chloé", "DeMellier",
    "Deveaux New York", "Dolce&Gabbana", "Fendi", "Ferragamo",
    "Givenchy", "Golden Goose", "Gucci", "Jacquemus", "Jimmy Choo",
    "Kenzo", "Mach & Mach", "Maison Margiela", "Off-White",
    "Oscar de la Renta", "Palm Angels", "Prada", "Saint Laurent",
    "Savette", "Self-Portrait", "Stella McCartney", "The Row",
    "Tory Burch", "Valentino", "Valentino Garavani", "Versace",
    "Vivienne Westwood",
]

MEN_DESIGNERS = [
    "Ami Paris", "Amiri", "Brunello Cucinelli", "Burberry",
    "Canada Goose", "Fendi", "Givenchy", "Gucci", "Jacquemus",
    "Kenzo", "Lanvin", "Loewe", "Maison Margiela", "Missoni",
    "New Balance", "Polo Ralph Lauren", "Prada", "Saint Laurent",
    "The North Face", "The Row", "Tod's", "Valentino",
    "Valentino Garavani", "Versace",
]

SECTIONS = [
    {
        "section":    "women",
        "slug":       "/sale",
        "gender_tag": "women",
        "designers":  WOMEN_DESIGNERS,
    },
    {
        "section":    "men",
        "slug":       "/sale",
        "gender_tag": "men",
        "designers":  MEN_DESIGNERS,
    },
]


def _make_headers(section: str) -> dict:
    return {
        "Accept-Language":  "en",
        "Content-Type":     "application/json",
        "Accept":           "*/*",
        "Origin":           "https://www.mytheresa.com",
        "Referer":          "https://www.mytheresa.com/",
        "User-Agent":       (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
        "X-Country":  "DE",
        "X-Nsu":      "false",
        "X-Op":       "ntr",
        "X-Region":   "DE",
        "X-Section":  section,
        "X-Store":    "euro",
    }


def _fetch_section_pages(section: str, slug: str, designers: list, stop_event=None) -> list:
    headers   = _make_headers(section)
    base_vars = {
        "designers": designers,
        "page":      1,
        "size":      120,
        "slug":      slug,
        "sort":      "recommendation",
    }

    def _post(page_num):
        v    = {**base_vars, "page": page_num}
        resp = requests.post(
            MYTHERESA_API,
            headers=headers,
            json={"query": GRAPHQL_QUERY, "variables": v},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    first = _post(1)
    pages = [first]
    plp   = (first.get("data") or {}).get("xProductListingPage") or {}
    pagination  = plp.get("pagination") or {}
    total_pages = pagination.get("totalPages", 1)
    print(f"[MyTheresa] {section} sale: {pagination.get('totalItems',0)} items, {total_pages} pages")

    for p in range(2, total_pages + 1):
        if stop_event and stop_event.is_set():
            break
        try:
            pages.append(_post(p))
            time.sleep(0.8)
        except Exception as exc:
            print(f"[MyTheresa] Error on {section} page {p}: {exc}")
    return pages


_DETAIL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def _normalize_img_url(url: str, size: int = 1000, quality: int = 95) -> str:
    """Upscale an img.mytheresa.com URL to the requested resolution."""
    decoded = url.replace('\\u002F', '/').replace('\\u002f', '/')
    result = re.sub(
        r'https://img\.mytheresa\.com/\d+/\d+/\d+/\w+/catalog/product/',
        f'https://img.mytheresa.com/{size}/{size}/{quality}/jpeg/catalog/product/',
        decoded, count=1,
    )
    return result


# All known Mytheresa CDN image angle suffixes (confirmed by CDN HEAD probing).
# Front (.jpg) is always present; _b1/_b2 = back views; _d1–_d7 = detail/lifestyle angles.
_CDN_SUFFIXES = [
    ".jpg", "_b1.jpg", "_d1.jpg", "_d2.jpg", "_d3.jpg",
    "_d4.jpg", "_b2.jpg", "_d5.jpg", "_d6.jpg", "_d7.jpg",
    "_b3.jpg", "_b4.jpg", "_d8.jpg", "_d9.jpg",
]

# Matches the folder + base-ID from a Mytheresa CDN catalog URL.
# The displayImages from the GraphQL API are already decoded (real slashes).
_CDN_BASE_RE = re.compile(
    r'catalog/product/(\w{2})/([\w]+?)(?:_\w+)?\.(?:jpg|jpeg|png|webp)',
    re.IGNORECASE,
)


def _probe_product_images(display_images: list) -> list:
    """
    Root-cause fix: Mytheresa's listing API only exposes 2 `displayImages` per
    product; extra gallery angles are loaded client-side by their Vue SPA and
    are NOT present in the SSR HTML (URLs are also \\u002F-encoded, breaking
    every regex approach).

    Instead we hit the CDN directly with HEAD requests for every known angle
    suffix (_b1, _d1 … _d7, _b2). The CDN returns 200 for existing angles and
    403/404 for missing ones — no rate-limits, very fast (~80 ms per probe).
    With 30 workers we process 6 k products in under 3 minutes.
    """
    if not display_images:
        return []

    first = display_images[0] if isinstance(display_images[0], str) else ""
    m = _CDN_BASE_RE.search(first)
    if not m:
        return [_normalize_img_url(u) for u in display_images if u]

    folder  = m.group(1)   # e.g. "4d"
    base_id = m.group(2)   # e.g. "P01165736"
    cdn_pfx = (
        f"https://img.mytheresa.com/1000/1000/95/jpeg/catalog/product/{folder}"
    )

    found: list = []
    sess = requests.Session()
    for suf in _CDN_SUFFIXES:
        url = f"{cdn_pfx}/{base_id}{suf}"
        try:
            r = sess.head(url, timeout=6, allow_redirects=True)
            if r.status_code == 200:
                found.append(url)
        except Exception:
            continue

    return found if found else [_normalize_img_url(u) for u in display_images if u]


def _enhance_product_images(products: list, stop_event=None, progress_callback=None) -> list:
    """
    For each product, probe the Mytheresa CDN for every image angle and
    replace the 2-image displayImages with the full gallery.
    Uses 30 workers (HEAD requests are very lightweight).
    """
    total = len(products)
    done  = 0
    enhanced: dict = {}

    def _work(product):
        slug = product["Handle"]
        display_imgs: list = []
        for v in product.get("variants", []):
            imgs = v.get("images") or []
            if imgs:
                display_imgs = imgs
                break
        probed = _probe_product_images(display_imgs)
        return slug, probed

    with ThreadPoolExecutor(max_workers=30) as pool:
        futures = {pool.submit(_work, p): p for p in products}
        for fut in as_completed(futures):
            if stop_event and stop_event.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            slug, imgs = fut.result()
            if imgs:
                enhanced[slug] = imgs
            done += 1
            if progress_callback and done % 100 == 0:
                pct = 55 + int((done / total) * 25)
                progress_callback(pct, f"Fetching product images: {done}/{total}…", total)

    for p in products:
        slug = p["Handle"]
        imgs = enhanced.get(slug)
        if imgs:
            for v in p.get("variants", []):
                v["images"] = imgs
    return products


def _clean_pages(pages: list, gender_tag: str) -> list:
    cleaned: dict = {}

    for page_data in pages:
        raw_products = (
            ((page_data.get("data") or {})
             .get("xProductListingPage") or {})
            .get("products") or []
        )

        for product in raw_products:
            if not product.get("hasStock", False):
                continue
            if not product.get("isPurchasable", True):
                continue

            handle = product.get("slug") or ""
            if not handle:
                continue

            title  = product.get("name", "").strip()
            brand  = (
                product.get("designerInfo", {}).get("displayName")
                or product.get("designer", "")
                or ""
            ).strip()
            combined = product.get("combinedCategoryName", "") or ""
            parts    = [p.strip() for p in combined.split("::") if p.strip()]
            p_type   = parts[-1] if parts else ""

            raw_desc = (product.get("description") or "").strip()
            color  = (product.get("color") or "").strip()

            tags_str = build_full_tags(title, brand, gender_tag, p_type,
                                       extra_tags=["RudraScrapper-mytheresa"])

            images: list = []
            seen_imgs: set = set()
            for img in (product.get("displayImages") or []):
                if img and img not in seen_imgs:
                    seen_imgs.add(img)
                    images.append(_normalize_img_url(img))

            if handle not in cleaned:
                cleaned[handle] = {
                    "Handle":       handle,
                    "Title":        title,
                    "Body (HTML)":  build_mirage_description(raw_desc, title, brand or "Mytheresa", gender_tag),
                    "Vendor":       brand or "Mytheresa",
                    "Type":         p_type,
                    "Tags":         tags_str,
                    "_color_name":  color,
                    "variants":     [],
                }

            seen_skus: set = set()
            for variant in (product.get("variants") or []):
                avail = variant.get("availability") or {}
                if not avail.get("hasStock", False):
                    continue
                stock_qty = (
                    avail.get("stockLevel")
                    or avail.get("qty")
                    or avail.get("quantity")
                )
                if stock_qty is not None and int(stock_qty) <= 0:
                    continue

                sku = (variant.get("sku") or "").strip()
                if not sku or sku in seen_skus:
                    continue
                seen_skus.add(sku)

                vp      = variant.get("price") or {}
                sale_c  = vp.get("discount") or vp.get("regular") or 0
                orig_c  = vp.get("original") or sale_c

                # X-Store=euro returns prices in EUR cents (e.g. 136500 = €1,365).
                # Fix: was X-Store=US (USD cents labeled "EUR") → INR prices inflated ~46%.
                sale_eur = round(sale_c  / 100, 2) if sale_c  else 0.0
                orig_eur = round(orig_c  / 100, 2) if orig_c  else 0.0

                if sale_eur <= 0:
                    continue

                size = (variant.get("size") or "").strip()
                if not size or size in ("-", "–", "—", "N/A", "n/a"):
                    size = "One Size"

                cleaned[handle]["variants"].append({
                    "Variant SKU":              sku,
                    "Variant Price":            sale_eur,
                    "Variant Compare At Price": orig_eur if orig_eur > sale_eur else "",
                    "currency":                 "EUR",
                    "size":                     size,
                    "images":                   images,
                })

    return [p for p in cleaned.values() if p["variants"]]


def complete_workflow_mytheresa(progress_callback=None, stop_event=None, **kwargs):
    def _cb(pct, msg, count=None):
        if progress_callback:
            try:
                progress_callback(pct, msg, count)
            except TypeError:
                progress_callback(pct, msg)

    scrape_record_id = start_scrape_record(SCRAPER_ID)

    heart_stop = threading.Event()

    def _heartbeat():
        while not heart_stop.is_set():
            try:
                heartbeat_scrape_record(scrape_record_id)
            except Exception:
                pass
            time.sleep(20)

    threading.Thread(target=_heartbeat, daemon=True).start()

    try:
        all_products: list = []
        total_sections = len(SECTIONS)

        for i, section_cfg in enumerate(SECTIONS):
            if stop_event and stop_event.is_set():
                update_scrape_record(scrape_record_id, status="cancelled")
                return

            section    = section_cfg["section"]
            slug       = section_cfg["slug"]
            gender_tag = section_cfg["gender_tag"]
            designers  = section_cfg["designers"]
            base_pct   = int(i * 80 / total_sections) + 5

            _cb(base_pct, f"Fetching MyTheresa {section} sale products…")

            pages = _fetch_section_pages(section, slug, designers, stop_event=stop_event)
            _cb(base_pct + int(35 / total_sections), f"Cleaning {section} data…")

            section_products = _clean_pages(pages, gender_tag)

            _cb(
                base_pct + int(35 / total_sections),
                f"{section}: {len(section_products)} raw products — fetching full images…",
                len(all_products) + len(section_products),
            )
            section_products = _enhance_product_images(
                section_products, stop_event=stop_event, progress_callback=_cb
            )
            all_products.extend(section_products)

            _cb(
                base_pct + int(38 / total_sections),
                f"{section}: {len(section_products)} products with full images",
                len(all_products),
            )

        if stop_event and stop_event.is_set():
            update_scrape_record(scrape_record_id, status="cancelled")
            return

        _cb(85, f"Saving {len(all_products)} products to database…", len(all_products))
        upsert_all_product_data(all_products, SCRAPER_ID, CURRENCY)

        csv_path = f"scraped_files/{SCRAPER_ID}_latest.csv"
        os.makedirs("scraped_files", exist_ok=True)

        _cb(90, "Generating Shopify CSV…", len(all_products))
        rows = transform_to_shopify(all_products)
        export_shopify_csv(rows, csv_path)

        _cb(96, "Uploading CSV…", len(all_products))
        csv_url = upload_csv_to_supabase(csv_path, SCRAPER_ID)

        update_scrape_record(
            scrape_record_id,
            status="completed",
            products_count=len(all_products),
            csv_url=csv_url,
        )
        _cb(100, "Done ✅", len(all_products))

    except Exception as exc:
        import traceback
        traceback.print_exc()
        update_scrape_record(scrape_record_id, status="failed", error_message=str(exc))
        raise
    finally:
        heart_stop.set()


if __name__ == "__main__":
    complete_workflow_mytheresa()
