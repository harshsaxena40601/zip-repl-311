"""
Master upload coordinator for main store.
- Runs MK + Mytheresa scraper re-runs in background threads
- Uploads SKIMS → Coach → Cruise Fashion (fresh CSVs) immediately
- Uploads MK and Mytheresa after their re-runs complete
- Logs everything to /tmp/upload_main_all.log

Run: nohup python3 scripts/upload_main_all.py >> /tmp/upload_main_all.log 2>&1 &
"""
import sys, os, time, logging, threading
sys.path.insert(0, '/home/runner/workspace')
os.chdir('/home/runner/workspace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('/tmp/upload_main_all.log', mode='a'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('upload_main')
for n in ['urllib3', 'requests', 'httpx', 'selenium', 'nodriver', 'undetected']:
    logging.getLogger(n).setLevel(logging.WARNING)

from core.shopify_publisher import upload_products, _set_store_key, _shopify_request

_set_store_key('main')

# ── Scraper re-run state ──────────────────────────────────────────────────────
scraper_done  = {}   # sid → True when re-run + CSV ready
scraper_error = {}   # sid → error string if failed
scraper_lock  = threading.Lock()


def run_scraper(sid):
    """Run scraper in background, update CSV, signal done."""
    log.info(f"[{sid}] Scraper re-run starting…")
    try:
        if sid == 'michael_kors':
            from scrapers.michael_kors.michael_kors import complete_workflow_michael_kors
            complete_workflow_michael_kors(progress_callback=None, stop_event=None)
        elif sid == 'mytheresa':
            from scrapers.mytheresa.mytheresa import complete_workflow_mytheresa
            complete_workflow_mytheresa(progress_callback=None, stop_event=None)
        log.info(f"[{sid}] Scraper re-run COMPLETE ✅")
        with scraper_lock:
            scraper_done[sid] = True
    except Exception as e:
        log.error(f"[{sid}] Scraper re-run FAILED: {e}", exc_info=False)
        with scraper_lock:
            scraper_done[sid] = False
            scraper_error[sid] = str(e)


def upload_one(sid, csv_path):
    """Upload a single scraper's CSV to main store."""
    if not os.path.exists(csv_path):
        log.error(f"[{sid}] CSV not found: {csv_path}")
        return {'error': 'csv_not_found'}

    size_mb = os.path.getsize(csv_path) / 1024 / 1024
    log.info(f"\n{'='*65}")
    log.info(f"  UPLOAD START  {sid.upper()}  ({size_mb:.1f} MB)")
    log.info(f"{'='*65}")

    cnt_before = _shopify_request('GET', '/products/count.json',
                                  params={'tag': f'RudraScrapper-{sid}'})
    log.info(f"  [{sid}] On main before: {cnt_before.get('count','?')}")

    def cb(pct, msg, n, extra=None):
        e = extra or {}
        log.info(f"  [{sid}] [{pct:3d}%] {msg}  "
                 f"created={e.get('created',0)} skipped={e.get('skipped',0)} "
                 f"failed={e.get('failed',0)}")

    t0 = time.time()
    _set_store_key('main')
    try:
        result = upload_products(sid, csv_path, progress_callback=cb)
    except Exception as e:
        log.error(f"  [{sid}] Upload FAILED: {e}", exc_info=True)
        return {'error': str(e)}

    elapsed = time.time() - t0
    cnt_after = _shopify_request('GET', '/products/count.json',
                                 params={'tag': f'RudraScrapper-{sid}'})
    log.info(f"  [{sid}] DONE in {elapsed/60:.1f}m  result={result}")
    log.info(f"  [{sid}] On main after:  {cnt_after.get('count','?')}")
    return result


# ── Phase 1: Start MK + Mytheresa scraper re-runs in background ──────────────
log.info("Phase 1: Starting MK + Mytheresa scraper re-runs in background…")
for sid in ('michael_kors', 'mytheresa'):
    t = threading.Thread(target=run_scraper, args=(sid,), daemon=True, name=f'scraper-{sid}')
    t.start()
time.sleep(2)

# ── Phase 2: Upload fresh scrapers immediately ────────────────────────────────
log.info("\nPhase 2: Uploading SKIMS, Coach, Cruise Fashion (fresh CSVs)…")
IMMEDIATE = [
    ('skims',          'scraped_files/skims_latest.csv'),
    ('coach',          'scraped_files/coach_latest.csv'),
    ('cruise_fashion', 'scraped_files/cruise_fashion_latest.csv'),
]
results = {}
for sid, csv_path in IMMEDIATE:
    results[sid] = upload_one(sid, csv_path)
    time.sleep(3)

# ── Phase 3: Wait for MK + Mytheresa scrapers, then upload ───────────────────
log.info("\nPhase 3: Waiting for MK + Mytheresa scraper re-runs…")
DELAYED = [('michael_kors', 'scraped_files/michael_kors_latest.csv'),
           ('mytheresa',    'scraped_files/mytheresa_latest.csv')]

for sid, csv_path in DELAYED:
    # Poll until scraper signals done (or give up after 3 hours)
    max_wait = 3 * 3600
    waited = 0
    while waited < max_wait:
        with scraper_lock:
            done = sid in scraper_done
        if done:
            break
        log.info(f"  [{sid}] Waiting for scraper re-run… ({waited//60}m elapsed)")
        time.sleep(120)
        waited += 120

    with scraper_lock:
        ok = scraper_done.get(sid, None)

    if ok is False:
        log.warning(f"  [{sid}] Scraper re-run failed: {scraper_error.get(sid,'?')} — uploading with existing CSV")
    elif ok is True:
        log.info(f"  [{sid}] Scraper re-run finished — uploading fresh CSV")
    else:
        log.warning(f"  [{sid}] Scraper re-run timed out after 3h — uploading with existing CSV")

    results[sid] = upload_one(sid, csv_path)
    time.sleep(3)

# ── Summary ──────────────────────────────────────────────────────────────────
log.info(f"\n{'='*65}")
log.info("  MASTER UPLOAD COMPLETE")
log.info(f"{'='*65}")
for sid, r in results.items():
    log.info(f"  {sid:<20} {r}")
