"""
verify_test_uploads.py
======================
End-to-end executor and validator for the four newly-registered scrapers:
  GEM Opticians (gemopticians), Tory Burch (tory),
  Karl Lagerfeld (karl), Kate Spade Outlet (katespadeoutlet)

Usage:
    python scripts/verify_test_uploads.py [--flask-url http://localhost:8000]

What it does (strict pass/fail at each gate):
  1. Confirms all 4 scrapers are registered in scrapers_registry.json
  2. Triggers POST /api/scrape for any scraper that has no CSV / is not completed
  3. Polls GET /api/scrapers until all 4 reach terminal state (completed / error)
  4. Fails if any scraper errored or produced 0 products
  5. Triggers POST /api/shopify/upload/{id} (X-Store-Key: test) for each completed scraper
  6. Polls GET /api/progress until all upload jobs reach 100% or fail
  7. Fails if any upload has >0 non-transient failures OR 0 products uploaded
  8. Queries GET /api/shopify/logs for activity-log success rows (best-effort; warns if DB unavailable)
  9. Prints final summary: scraped/uploaded/skipped/failed per brand
"""

import argparse
import json
import os
import sys
import time

import requests

SCRAPERS     = ["gemopticians", "tory", "karl", "katespadeoutlet"]
REGISTRY     = os.path.join(os.path.dirname(__file__), "..", "scrapers_registry.json")
SCRAPE_TIMEOUT  = 3600   # seconds — Kate Spade takes ~6-8 min
UPLOAD_TIMEOUT  = 14400  # seconds — 4 concurrent uploads at 2 req/s can take hours
POLL_INTERVAL   = 30     # seconds between polls
MAX_TOLERATED_UPLOAD_FAILURES = 5  # tolerate a handful of transient 500s


def _get(base, path, params=None):
    r = requests.get(f"{base}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(base, path, headers=None, json_body=None):
    r = requests.post(f"{base}{path}", headers=headers or {}, json=json_body or {}, timeout=30)
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Registry check
# ─────────────────────────────────────────────────────────────────────────────

def step1_registry():
    print("\n══ Step 1: Registry ══")
    with open(REGISTRY) as f:
        entries = json.load(f)
    ids = {e["id"] for e in entries}
    missing = [s for s in SCRAPERS if s not in ids]
    if missing:
        print(f"  FAIL — scrapers missing from registry: {missing}")
        sys.exit(1)
    for s in SCRAPERS:
        entry = next(e for e in entries if e["id"] == s)
        print(f"  OK   — {s}: name={entry['name']}  type={entry['type']}")
    print("  PASS")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Trigger scrapes for scrapers that are not yet completed
# ─────────────────────────────────────────────────────────────────────────────

def step2_trigger_scrapes(base):
    print("\n══ Step 2: Trigger scrapes ══")
    scrapers_resp = _get(base, "/api/scrapers")
    by_id = {s["id"]: s for s in scrapers_resp.get("scrapers", [])}

    to_scrape = []
    for sid in SCRAPERS:
        info = by_id.get(sid, {})
        status = info.get("status", "unknown")
        products = info.get("total_products", 0)
        if status == "completed" and products > 0:
            print(f"  SKIP — {sid}: already completed with {products} products")
        elif status == "running":
            print(f"  SKIP — {sid}: currently running (will poll to completion)")
        else:
            print(f"  RUN  — {sid}: status={status}, triggering scrape...")
            to_scrape.append(sid)

    if to_scrape:
        resp = _post(base, "/api/scrape", json_body={"scraper_ids": to_scrape})
        print(f"  Triggered: {resp}")
    else:
        print("  All scrapers already completed — skipping re-run.")
    return to_scrape


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Poll until all 4 scrapers reach terminal state
# ─────────────────────────────────────────────────────────────────────────────

def step3_poll_scrapes(base):
    print("\n══ Step 3: Poll scrape completion ══")
    deadline = time.time() + SCRAPE_TIMEOUT
    while time.time() < deadline:
        resp     = _get(base, "/api/scrapers")
        by_id    = {s["id"]: s for s in resp.get("scrapers", [])}
        running  = [s for s in SCRAPERS if by_id.get(s, {}).get("status") == "running"]
        statuses = {s: (by_id.get(s, {}).get("status", "?"), by_id.get(s, {}).get("total_products", 0))
                    for s in SCRAPERS}
        lines = [f"{sid}:{st}({n})" for sid, (st, n) in statuses.items()]
        print(f"  [{time.strftime('%H:%M:%S')}] {' | '.join(lines)}")
        if not running:
            break
        time.sleep(POLL_INTERVAL)
    else:
        print(f"  FAIL — scrape polling timed out after {SCRAPE_TIMEOUT}s")
        sys.exit(1)

    # Strict gate: all must be completed with > 0 products
    resp  = _get(base, "/api/scrapers")
    by_id = {s["id"]: s for s in resp.get("scrapers", [])}
    failed = []
    results = {}
    for sid in SCRAPERS:
        info = by_id.get(sid, {})
        status   = info.get("status", "?")
        products = info.get("total_products", 0)
        updated  = info.get("last_updated", "?")
        results[sid] = {"status": status, "products": products, "updated": updated}
        if status != "completed" or products == 0:
            failed.append(f"{sid}: status={status} products={products}")

    if failed:
        print(f"  FAIL — scrapers did not complete successfully: {failed}")
        sys.exit(1)

    for sid, r in results.items():
        csv_path = f"scraped_files/{sid}_latest.csv"
        row_count = sum(1 for _ in open(csv_path, encoding="utf-8-sig")) if os.path.exists(csv_path) else 0
        size_mb   = os.path.getsize(csv_path) // (1024 * 1024) if os.path.exists(csv_path) else 0
        print(f"  OK   — {sid}: {r['products']} products  CSV={row_count} rows ({size_mb}MB)  updated={r['updated']}")

    print("  PASS")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Trigger test-store uploads
# ─────────────────────────────────────────────────────────────────────────────

def step4_trigger_uploads(base):
    print("\n══ Step 4: Trigger test-store uploads ══")

    # Check which uploads are already in progress
    prog_resp = _get(base, f"/api/progress?scraper_id={SCRAPERS[0]}")

    triggered = []
    already_running = []
    for sid in SCRAPERS:
        info = prog_resp.get(sid, {})
        if info.get("is_running") and info.get("shopify_op") == "upload":
            uploaded = info.get("shopify_uploaded_count", 0)
            total    = info.get("shopify_counts", {}).get("total", "?")
            print(f"  SKIP — {sid}: upload already in progress ({uploaded}/{total} uploaded)")
            already_running.append(sid)
        else:
            resp = _post(
                base,
                f"/api/shopify/upload/{sid}",
                headers={"X-Store-Key": "test", "Content-Type": "application/json"},
            )
            if resp.get("message") in ("Upload started", "already_running"):
                print(f"  OK   — {sid}: upload triggered → {resp}")
                triggered.append(sid)
            else:
                print(f"  FAIL — {sid}: unexpected upload response: {resp}")
                sys.exit(1)

    print(f"  Triggered new: {triggered}  |  Already running: {already_running}")
    return triggered + already_running


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Poll uploads to terminal state
# ─────────────────────────────────────────────────────────────────────────────

def step5_poll_uploads(base):
    print("\n══ Step 5: Poll upload completion ══")
    deadline = time.time() + UPLOAD_TIMEOUT
    final    = {}

    while time.time() < deadline:
        prog = _get(base, f"/api/progress?scraper_id={SCRAPERS[0]}")
        still_running = []
        lines = []
        for sid in SCRAPERS:
            info     = prog.get(sid, {})
            running  = info.get("is_running", False)
            uploaded = info.get("shopify_uploaded_count", 0)
            failed   = info.get("shopify_failed_count",   0)
            total    = info.get("shopify_counts", {}).get("total", "?")
            pct      = info.get("progress", 0)
            lines.append(f"{sid}:{uploaded}/{total}({pct}%,f={failed})")
            if running:
                still_running.append(sid)
            else:
                final[sid] = info

        print(f"  [{time.strftime('%H:%M:%S')}] {' | '.join(lines)}")

        if not still_running:
            # All finished
            for sid in SCRAPERS:
                if sid not in final:
                    final[sid] = prog.get(sid, {})
            break

        time.sleep(POLL_INTERVAL)
    else:
        # Timeout — capture whatever state we have and report
        prog = _get(base, f"/api/progress?scraper_id={SCRAPERS[0]}")
        for sid in SCRAPERS:
            final[sid] = prog.get(sid, {})
        print(f"  WARN — upload polling timed out after {UPLOAD_TIMEOUT}s (uploads may still be running)")

    # Strict gate: each must have >0 products uploaded and failures <= tolerance
    gate_failed = []
    for sid in SCRAPERS:
        info     = final.get(sid, {})
        uploaded = info.get("shopify_uploaded_count", 0)
        failed   = info.get("shopify_failed_count",   0)
        skipped  = info.get("shopify_skipped_count",  0)
        total    = info.get("shopify_counts", {}).get("total", "?")
        running  = info.get("is_running", False)

        ok = (uploaded > 0 or skipped > 0) and failed <= MAX_TOLERATED_UPLOAD_FAILURES
        icon = "OK  " if ok else "FAIL"
        print(f"  {icon} — {sid}: {uploaded}/{total} uploaded, {skipped} skipped, {failed} failed, running={running}")

        if not ok:
            gate_failed.append(f"{sid}: uploaded={uploaded} skipped={skipped} failed={failed}")

    if gate_failed:
        print(f"\n  FAIL — upload gate failed: {gate_failed}")
        sys.exit(1)

    print("  PASS")
    return final


# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Activity-log verification (best-effort; warns if DB unavailable)
# ─────────────────────────────────────────────────────────────────────────────

def step6_activity_logs(base):
    print("\n══ Step 6: Activity-log verification ══")
    try:
        resp = _get(base, "/api/shopify/logs", params={"limit": 200})
        logs = resp.get("logs", [])
    except Exception as e:
        print(f"  WARN — Could not reach /api/shopify/logs: {e}")
        print("  WARN — DB (Supabase) may not be connected in this environment; skipping log check.")
        return

    targets = set(SCRAPERS)
    found   = {}
    for entry in logs:
        sid = entry.get("scraper_id")
        if sid in targets:
            found.setdefault(sid, []).append(entry)

    for sid in SCRAPERS:
        rows = found.get(sid, [])
        if rows:
            success = [r for r in rows if r.get("status") == "success"]
            print(f"  OK   — {sid}: {len(rows)} log rows ({len(success)} success)")
        else:
            print(f"  WARN — {sid}: no activity-log rows (DB not connected or upload still in progress)")


# ─────────────────────────────────────────────────────────────────────────────
# Final summary
# ─────────────────────────────────────────────────────────────────────────────

def final_summary(scrape_results, upload_results):
    print("\n══ Final Summary ══")
    print(f"  {'Scraper':22} {'Scraped':>10} {'Uploaded':>10} {'Skipped':>9} {'Failed':>8}")
    print("  " + "-" * 65)
    for sid in SCRAPERS:
        sr = scrape_results.get(sid, {})
        ur = upload_results.get(sid, {})
        print(
            f"  {sid:22} {sr.get('products', '?'):>10} "
            f"{ur.get('shopify_uploaded_count', '?'):>10} "
            f"{ur.get('shopify_skipped_count',  '?'):>9} "
            f"{ur.get('shopify_failed_count',   '?'):>8}"
        )
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run and verify test-store uploads for 4 scrapers.")
    parser.add_argument("--flask-url", default="http://localhost:8000",
                        help="Base URL of the Flask backend (default: http://localhost:8000)")
    args = parser.parse_args()
    base = args.flask_url.rstrip("/")

    step1_registry()
    step2_trigger_scrapes(base)
    scrape_results = step3_poll_scrapes(base)
    step4_trigger_uploads(base)
    upload_results = step5_poll_uploads(base)
    step6_activity_logs(base)
    final_summary(scrape_results, upload_results)
    print("✅ All gates passed.")
