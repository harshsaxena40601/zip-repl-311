"""
Gemini Tag Verifier
===================
Validates Coach product tags in coach_latest.csv using Google Gemini.

Usage:
    python tools/gemini_tag_verify.py [--sample N] [--csv PATH] [--fix]

Requirements:
    GEMINI_API_KEY environment variable must be set.

Options:
    --sample N     Number of products to verify (default: 50)
    --csv PATH     Path to CSV file (default: scraped_files/coach_latest.csv)
    --fix          Write corrected tags back to CSV automatically
"""

import argparse
import csv
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


VALID_WOMEN_TAGS = {
    "womens-shoulderbags", "womens-handbags", "womens-totebags",
    "womens-minibag", "womens-crossbodybag",
    "womens-footwear", "womens-sneakers", "womens-loafers", "womens-flats",
    "womens-heels",
    "womens-accessories", "womens-smallaccessories", "womens-belts",
    "womens-watches", "womens-jewellery",
    "women-apparel", "womens-topsandsportsbra", "womens-leggings",
    "womens-winterwear", "womens-co-ordsets",
}
VALID_MEN_TAGS = {
    "mens-bags", "mens-accessories", "mens-wallets", "mens-belts",
    "mens-jewellery",
    "mens-footwear", "mens-sneakers", "mens-loafers", "mens-slides",
    "Men-apparel", "men-tshirts", "men-shirt", "men-polo",
    "men-winterwear",
}
VALID_BROAD = {"bags", "footwear", "accessories", "apparel", "watches"}


def build_prompt(products: list[dict]) -> str:
    lines = []
    for p in products:
        lines.append(
            f"- Title: \"{p['title']}\"\n"
            f"  Current tags: {p['tags']}"
        )
    joined = "\n".join(lines)

    return f"""You are a luxury eCommerce merchandising assistant for a Shopify store.

Your job is to verify whether the Shopify tags assigned to each product are correct.

TAG RULES:
1. Each product gets exactly ONE gender tag: "women", "men", or both "men"+"women"+"unisex".
2. Each product gets exactly ONE broad category tag: bags | footwear | accessories | apparel | watches.
3. Each product gets exactly ONE specific taxonomy tag:
   - Bags (women): womens-shoulderbags | womens-handbags | womens-totebags | womens-minibag | womens-crossbodybag
   - Bags (men): mens-bags
   - Footwear (women): womens-sneakers | womens-loafers | womens-flats | womens-heels
   - Footwear (men): mens-sneakers | mens-loafers | mens-slides
   - Accessories (women): womens-smallaccessories | womens-belts | womens-watches | womens-jewellery
   - Accessories (men): mens-wallets | mens-belts | mens-jewellery
   - Apparel (women): womens-topsandsportsbra | womens-leggings | womens-winterwear | womens-co-ordsets
   - Apparel (men): men-tshirts | men-shirt | men-polo | men-winterwear
4. A shoulder bag MUST have "womens-shoulderbags" only — NOT "womens-handbags".
5. A tote MUST have "womens-totebags" only — NOT "womens-handbags".
6. A crossbody or belt bag MUST have "womens-crossbodybag" only.
7. A generic "bag" (no specific style) uses "womens-handbags".

Products to verify:
{joined}

For each product, respond in this JSON format only (no markdown, no extra text):
[
  {{
    "title": "<product title>",
    "correct": true/false,
    "issues": "<brief description if incorrect, else empty string>",
    "fixed_tags": "<corrected comma-separated tags if incorrect, else empty string>"
  }}
]"""


def verify_with_gemini(products: list[dict], api_keys: list[str], model: str = "gemini-2.0-flash") -> list[dict]:
    from google import genai
    from google.genai import types
    from google.genai.errors import ClientError

    results = []
    batch_size = 10
    key_idx = 0

    def get_client():
        return genai.Client(api_key=api_keys[key_idx % len(api_keys)])

    for i in range(0, len(products), batch_size):
        batch = products[i:i + batch_size]
        prompt = build_prompt(batch)
        success = False
        for attempt in range(len(api_keys) * 2):
            try:
                client = get_client()
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.1),
                )
                text = response.text.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                parsed = json.loads(text)
                results.extend(parsed)
                success = True
                break
            except ClientError as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    key_idx += 1
                    wait = 15 * (attempt + 1)
                    print(f"[WARN] Rate limited, rotating key / waiting {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                else:
                    print(f"[WARN] Gemini API error: {e}", file=sys.stderr)
                    break
            except Exception as e:
                print(f"[WARN] Gemini error on batch {i//batch_size + 1}: {e}", file=sys.stderr)
                break
        if not success:
            for p in batch:
                results.append({"title": p["title"], "correct": True, "issues": "", "fixed_tags": ""})
        if i + batch_size < len(products):
            time.sleep(3)

    return results


def main():
    parser = argparse.ArgumentParser(description="Verify Coach product tags with Gemini")
    parser.add_argument("--sample", type=int, default=50, help="Number of products to check")
    parser.add_argument("--csv", default="scraped_files/coach_latest.csv", help="CSV file path")
    parser.add_argument("--fix", action="store_true", help="Write corrections back to CSV")
    parser.add_argument("--model", default="gemini-2.0-flash", help="Gemini model to use")
    args = parser.parse_args()

    # Collect all available keys (GEMINI_API_KEY, GEMINI_API_KEYS_1, GEMINI_API_KEYS_2, ...)
    api_keys = []
    for var in ["GEMINI_API_KEY", "GEMINI_API_KEYS_1", "GEMINI_API_KEYS_2", "GEMINI_API_KEYS_3"]:
        k = os.environ.get(var, "").strip()
        if k and k not in api_keys:
            api_keys.append(k)
    if not api_keys:
        print("ERROR: No Gemini API key found. Set GEMINI_API_KEY in environment.", file=sys.stderr)
        sys.exit(1)
    print(f"Using {len(api_keys)} API key(s)")

    # Load unique products (by handle)
    products = []
    seen_handles = set()
    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            h = row.get("Handle", "")
            title = row.get("Title", "") or ""
            tags = row.get("Tags", "") or ""
            if title and h and h not in seen_handles:
                seen_handles.add(h)
                products.append({"handle": h, "title": title, "tags": tags})

    sample_size = min(args.sample, len(products))
    sample = random.sample(products, sample_size)
    print(f"Verifying {sample_size} products with Gemini ({args.model})...")

    results = verify_with_gemini(sample, api_keys, args.model)

    wrong = [r for r in results if not r.get("correct")]
    print(f"\nResults: {len(results)} checked, {len(wrong)} issues found\n")

    if wrong:
        print("Issues:")
        for r in wrong:
            print(f"  [{r['title']}]")
            print(f"    Issue : {r.get('issues', '')}")
            print(f"    Fix   : {r.get('fixed_tags', '')}")
        print()

    if args.fix and wrong:
        fix_map = {r["title"]: r["fixed_tags"] for r in wrong if r.get("fixed_tags")}
        handle_to_tags = {p["handle"]: fix_map[p["title"]] for p in sample
                          if p["title"] in fix_map}

        tmp = args.csv + ".tmp"
        seen = {}
        with open(args.csv, newline="", encoding="utf-8") as fin, \
             open(tmp, "w", newline="", encoding="utf-8") as fout:
            reader = csv.DictReader(fin)
            writer = csv.DictWriter(fout, fieldnames=reader.fieldnames or [],
                                    quoting=csv.QUOTE_ALL, lineterminator="\n",
                                    extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                h = row.get("Handle", "")
                if row.get("Title") and h in handle_to_tags:
                    row["Tags"] = handle_to_tags[h]
                    seen[h] = handle_to_tags[h]
                elif h in seen:
                    row["Tags"] = seen[h]
                writer.writerow(row)
        os.replace(tmp, args.csv)
        print(f"Fixed {len(fix_map)} products in {args.csv}")

    if not wrong:
        print("All sampled tags look correct!")


if __name__ == "__main__":
    main()
