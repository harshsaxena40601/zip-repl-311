"""
Comprehensive re-tagging script for all scraper CSVs.

Uses the updated build_full_tags() from tag_engine.py to regenerate all tags
from scratch for every product row. Preserves RudraScrapper-* tags.

Fixes:
- accessories / daily-essential on apparel products (sweatshirts, etc.)
- womens-topsandsportsbra on dresses → womens-dresses
- womens-co-ordsets on jeans/pants → womens-bottomwear
- mens-tshirts on pants → mens-bottomwear
- Missing womens-dresses, womens-bottomwear, mens-bottomwear store tags
- Wrong gender tags on products where category keyword detection was broken
  (e.g. sandals/heels misclassified due to \b...\b regex failing on plurals)
"""

import csv
import io
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from core.tag_engine import build_full_tags

SCRAPERS = [
    "coach", "cruise_fashion", "karl", "michael_kors", "marcjacobs",
    "tory", "mytheresa", "uk_polene", "thedesignerboxuk", "hoka",
]

RUDRA_RE = re.compile(r'\bRudraScrapper-\S+', re.I)

def _extract_rudra(tags_str: str) -> str:
    m = RUDRA_RE.search(tags_str or "")
    return m.group(0) if m else ""

def retag_row(row: dict, rudra_tag: str) -> str:
    title       = row.get("Title", "")
    vendor      = row.get("Vendor", "")
    gender      = row.get("Google Shopping / Gender", "") or "unisex"
    product_type = row.get("Type", "") or row.get("Product Category", "")
    existing_tags = row.get("Tags", "")

    rudra = _extract_rudra(existing_tags) or rudra_tag
    extra = [rudra] if rudra else []

    return build_full_tags(title, vendor, gender, product_type, extra_tags=extra)

def process_csv(path: str, rudra_tag: str) -> tuple[int, int]:
    if not os.path.exists(path):
        return 0, 0

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows   = list(reader)
        fieldnames = reader.fieldnames or []

    if not rows:
        return 0, 0

    changed = 0
    seen_handles: set = set()

    for row in rows:
        handle = row.get("Handle", "")
        if not handle or not row.get("Title"):
            continue

        if handle in seen_handles:
            continue
        seen_handles.add(handle)

        old_tags = row.get("Tags", "")
        new_tags = retag_row(row, rudra_tag)

        if set(t.strip() for t in old_tags.split(",") if t.strip()) != \
           set(t.strip() for t in new_tags.split(",") if t.strip()):
            changed += 1

        row["Tags"] = new_tags

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    return len(seen_handles), changed


def main():
    total_products = 0
    total_changed  = 0

    for scraper_id in SCRAPERS:
        rudra_tag = f"RudraScrapper-{scraper_id}"
        paths = [
            f"scraped_files/{scraper_id}_latest.csv",
            f"exports/{scraper_id}_shopify.csv",
        ]
        scraper_products = 0
        scraper_changed  = 0
        for path in paths:
            prods, ch = process_csv(path, rudra_tag)
            scraper_products = max(scraper_products, prods)
            scraper_changed  = max(scraper_changed,  ch)

        total_products += scraper_products
        total_changed  += scraper_changed
        print(f"  {scraper_id:<22}: {scraper_products:>5} products, {scraper_changed:>5} tags changed")

    print(f"\nTotal: {total_products} products, {total_changed} tag sets changed")


if __name__ == "__main__":
    main()
