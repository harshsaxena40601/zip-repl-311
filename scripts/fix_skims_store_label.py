"""
One-time reconciliation: set store='main' for all Skims products that
are on the Main Shopify store but registered as store='test' in the DB.

Run AFTER the midnight upload completes:
    python scripts/fix_skims_store_label.py

Safe to re-run — uses ON CONFLICT DO UPDATE.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.shopify_publisher import _set_store_key, get_scraper_products
from core.db import get_connection

def main():
    print("Scanning Main store for RudraScrapper-skims products…")
    _set_store_key('main')
    products = get_scraper_products('skims')
    print(f"Found {len(products)} products on Main with RudraScrapper-skims tag")

    if not products:
        print("Nothing to reconcile.")
        return

    conn, cur = get_connection()
    if not conn:
        print("ERROR: no DB connection")
        return

    updated = 0
    inserted = 0
    for p in products:
        pid   = str(p['id'])
        handle = p.get('handle', '')
        title  = p.get('title', '')
        variants = p.get('variants', [])
        sku    = variants[0].get('sku', '') if variants else ''

        cur.execute("""
            INSERT INTO shopify_products
              (scraper_id, shopify_product_id, sku, handle, title, status, qa_status, store)
            VALUES ('skims', %s, %s, %s, %s, 'active', 'QA_PENDING_REVIEW', 'main')
            ON CONFLICT (shopify_product_id) DO UPDATE SET
              store          = 'main',
              last_synced_at = CURRENT_TIMESTAMP
            RETURNING (xmax = 0) AS was_inserted
        """, (pid, sku, handle, title))
        row = cur.fetchone()
        if row and row[0]:
            inserted += 1
        else:
            updated += 1

    conn.commit()
    cur.close()
    _return_connection(conn)

    print(f"\nDone — {inserted} new rows inserted, {updated} rows updated to store='main'")
    print("Skims DB registry is now accurate for the Main store.")


if __name__ == '__main__':
    from core.db import _return_connection
    main()
