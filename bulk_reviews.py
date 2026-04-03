import argparse
import json
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from scraper import CDP_URL, append_json, extract_reviews, load_seen_ids, wait_for_any_selector

RAW_DIR = Path("data/raw")
ASINS_FILE = Path("data/asins_by_brand.json")


def collect_asins_from_products(raw_dir: Path) -> dict[str, list[str]]:
    """Collect unique ASINs grouped by brand from *_products.json files."""
    result: dict[str, list[str]] = {}

    for path in sorted(raw_dir.glob("*_products.json")):
        brand = path.stem.replace("_products", "")
        try:
            with open(path, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[warn] Skipping unreadable product file: {path}")
            continue

        seen = set()
        asins: list[str] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                asin = row.get("asin")
                if asin and asin not in seen:
                    seen.add(asin)
                    asins.append(asin)

        if asins:
            result[brand] = asins

    return result


def save_asins_map(asins_map: dict[str, list[str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(asins_map, f, ensure_ascii=False, indent=2)


def load_asins_map(path: Path) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}

    cleaned: dict[str, list[str]] = {}
    for brand, asins in data.items():
        if isinstance(brand, str) and isinstance(asins, list):
            cleaned[brand] = [a for a in asins if isinstance(a, str) and a]
    return cleaned


def scrape_brand_reviews(page, brand: str, asins: list[str], pages_per_asin: int, wait_ms: int) -> tuple[int, int]:
    """Scrape reviews for a brand across multiple ASINs and pages."""
    out_path = RAW_DIR / f"{brand}_reviews.json"
    seen = load_seen_ids(out_path)
    total_saved = 0
    total_seen = len(seen)

    for idx, asin in enumerate(asins, start=1):
        reviews_url = f"https://www.amazon.in/product-reviews/{asin}?sortBy=recent&reviewerType=all_reviews"
        print(f"\n[{brand}] {idx}/{len(asins)} ASIN={asin}")
        print(f"  -> {reviews_url}")

        try:
            page.goto(reviews_url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeoutError:
            print("  [warn] page.goto timeout; skipping this ASIN")
            continue

        found = wait_for_any_selector(
            page,
            [
                '[data-hook="review"]',
                '#cm_cr-review_list',
                '#cm_cr-review_list_slot',
            ],
            timeout=15_000,
        )
        if not found:
            print("  [warn] Review selectors not detected; skipping this ASIN")
            continue

        page.wait_for_timeout(wait_ms)

        for page_no in range(1, pages_per_asin + 1):
            batch = extract_reviews(page, brand, asin)
            new = [r for r in batch if r.get("id") not in seen]
            for r in new:
                seen.add(r["id"])

            saved = append_json(out_path, new)
            total_saved += saved
            print(f"  page {page_no}: found={len(batch)} saved_new={saved}")

            if page_no >= pages_per_asin:
                break

            next_link = page.query_selector("li.a-last a")
            if not next_link:
                break

            href = next_link.get_attribute("href")
            if not href:
                break

            next_url = urljoin("https://www.amazon.in", href)
            try:
                page.goto(next_url, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightTimeoutError:
                print("  [warn] next-page goto timeout; stopping pagination for this ASIN")
                break
            page.wait_for_timeout(wait_ms)

    total_seen = len(seen)
    return total_saved, total_seen


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk scrape Amazon review pages for ASINs across brands")
    parser.add_argument("--brands", nargs="*", default=None, help="Optional list of brands to run")
    parser.add_argument("--max-asins-per-brand", type=int, default=8, help="Limit ASINs per brand")
    parser.add_argument("--pages-per-asin", type=int, default=2, help="Review pages to scrape per ASIN")
    parser.add_argument("--wait-ms", type=int, default=2500, help="Wait after page load (milliseconds)")
    parser.add_argument("--asins-file", default=str(ASINS_FILE), help="Path to save/load ASIN map JSON")
    parser.add_argument("--use-existing-asins", action="store_true", help="Use existing ASIN map file instead of rebuilding")
    parser.add_argument("--cdp-url", default=CDP_URL, help="Chrome CDP URL")
    args = parser.parse_args()

    asins_path = Path(args.asins_file)

    if args.use_existing_asins and asins_path.exists():
        asins_map = load_asins_map(asins_path)
    else:
        asins_map = collect_asins_from_products(RAW_DIR)
        save_asins_map(asins_map, asins_path)

    if not asins_map:
        raise SystemExit("No ASINs found. Run product scraping first.")

    selected_brands = args.brands if args.brands else sorted(asins_map.keys())

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp_url)
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("No browser contexts found. Is Chrome running with remote debugging?")
        context = contexts[0]
        page = context.new_page()

        print("\n=== BULK REVIEW SCRAPER START ===")
        print(f"Brands: {', '.join(selected_brands)}")
        print(f"ASIN map file: {asins_path}")

        grand_total_saved = 0
        for brand in selected_brands:
            brand_asins = asins_map.get(brand, [])
            if not brand_asins:
                print(f"\n[{brand}] no ASINs found, skipping")
                continue

            if args.max_asins_per_brand > 0:
                brand_asins = brand_asins[: args.max_asins_per_brand]

            saved, total_seen = scrape_brand_reviews(
                page=page,
                brand=brand,
                asins=brand_asins,
                pages_per_asin=max(1, args.pages_per_asin),
                wait_ms=max(500, args.wait_ms),
            )
            grand_total_saved += saved
            print(f"[{brand}] saved_new={saved} total_unique_reviews={total_seen}")

        print("\n=== DONE ===")
        print(f"Total newly saved reviews: {grand_total_saved}")


if __name__ == "__main__":
    main()
