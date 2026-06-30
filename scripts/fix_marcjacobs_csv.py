"""
Marc Jacobs CSV Re-Generator
=============================
Re-generates scraped_files/marcjacobs_latest.csv (and exports/marcjacobs_shopify.csv)
from the product data already stored in the database — without re-scraping the
Marc Jacobs website.

Usage:
    python scripts/fix_marcjacobs_csv.py

After running, trigger "Fix Images" from the dashboard to re-link colour variant
images on the live Shopify store.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import get_product_by_website, upload_csv_to_supabase, start_scrape_record, update_scrape_record
from core.shopify_transformer import transform_to_shopify, export_shopify_csv, fix_csv_inventory_rows

SCRAPER_ID = "marcjacobs"
CSV_LATEST = f"scraped_files/{SCRAPER_ID}_latest.csv"
CSV_EXPORT = f"exports/{SCRAPER_ID}_shopify.csv"


def fix_marcjacobs_csv():
    print(f"📦 Fetching {SCRAPER_ID} products from database…")
    row = get_product_by_website(SCRAPER_ID)
    if not row:
        print(f"❌ No product record found in DB for '{SCRAPER_ID}'.")
        sys.exit(1)

    products_data = row.get("products", {})
    if isinstance(products_data, str):
        products_data = json.loads(products_data)

    if isinstance(products_data, dict):
        products = products_data.get("products", [])
    else:
        products = products_data

    if not products:
        print("❌ Product list is empty in DB.")
        sys.exit(1)

    print(f"✅ Found {len(products)} products. Re-generating CSV with updated image fallback…")

    rows = transform_to_shopify(products)

    os.makedirs("scraped_files", exist_ok=True)
    os.makedirs("exports", exist_ok=True)

    export_shopify_csv(rows, CSV_LATEST)
    fix_csv_inventory_rows(CSV_LATEST)
    print(f"✅ Written {CSV_LATEST}")

    export_shopify_csv(rows, CSV_EXPORT)
    fix_csv_inventory_rows(CSV_EXPORT)
    print(f"✅ Written {CSV_EXPORT}")

    print("🚀 Uploading updated CSV to Supabase Storage…")
    try:
        csv_url = upload_csv_to_supabase(CSV_LATEST, SCRAPER_ID)
        record_id = start_scrape_record(SCRAPER_ID)
        update_scrape_record(record_id, status="completed", products_count=len(products), csv_url=csv_url)
        print(f"✅ Supabase upload complete: {csv_url}")
    except Exception as e:
        print(f"⚠️  Supabase upload skipped (no DB available): {e}")

    print(f"\n✨ Done! {len(products)} products, {len(rows)} CSV rows. Run 'Fix Images' from the dashboard to repair Shopify variant images.")


if __name__ == "__main__":
    fix_marcjacobs_csv()
