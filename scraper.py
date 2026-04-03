# USAGE:
#   1. Launch Chrome with remote debugging:
#      Windows: "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
#      Linux/Mac: google-chrome --remote-debugging-port=9222
#
#   2. In that Chrome, navigate to:
#      - Amazon SEARCH page:  https://www.amazon.in/s?k=safari+luggage
#      - Or REVIEWS page:     https://www.amazon.in/product-reviews/ASIN_HERE
#
#   3. Run this scraper:
#      uv run python scraper.py --mode search --brand safari
#      uv run python scraper.py --mode reviews --brand safari --asin B08XYZ1234
#
#   4. Scroll the page in Chrome, press 's' in terminal (or click the button) to save visible items.
#      Press 'q' to quit when done. Chrome stays open.
#
# OUTPUT:
#   data/raw/{brand}_products.json   — product listings
#   data/raw/{brand}_reviews.json    — reviews per product

import argparse
import json
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import select
except ImportError:
    select = None

CDP_URL = "http://127.0.0.1:9222"
DATA_DIR = Path("data/raw")
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_text(s: str) -> str:
    if not s:
        return ""
    return " ".join(s.strip().split())


def parse_price(text: str) -> float | None:
    """Extract numeric price from strings like '₹1,299' or '1,299.00'"""
    if not text:
        return None
    digits = ""
    for ch in text:
        if ch.isdigit() or ch == ".":
            digits += ch
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def append_json(path: Path, records: list) -> int:
    """Append records into a single JSON array file, deduplicating by 'id' field."""
    if not records:
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
    except FileNotFoundError:
        existing = []

    existing_ids = {r.get("id") for r in existing if r.get("id")}
    new_records = [r for r in records if r.get("id") not in existing_ids]
    existing.extend(new_records)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    return len(new_records)


def load_seen_ids(path: Path) -> set[str]:
    """Load already-saved record IDs from a JSON file using UTF-8 safely."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except FileNotFoundError:
        return set()
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"  [warn] Could not parse existing file at {path}. Continuing with empty seen set.")
        return set()

    seen: set[str] = set()
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("id"):
                seen.add(r["id"])
    return seen


def wait_for_any_selector(page, selectors: list[str], timeout: int = 30_000) -> str | None:
    """Return the first selector that appears, otherwise None."""
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=timeout)
            return selector
        except PlaywrightTimeoutError:
            continue
    return None


# ---------------------------------------------------------------------------
# Scrape: Search / listing page  →  product cards
# ---------------------------------------------------------------------------

def extract_search_results(page, brand: str) -> list[dict]:
    """
    Scrape product cards from an Amazon search results page.
    Collects: ASIN, title, price, MRP, discount, rating, review_count, url.
    """
    results = []

    # Each product card
    cards = page.query_selector_all('div[data-component-type="s-search-result"]')

    for card in cards:
        try:
            asin = card.get_attribute("data-asin")
            if not asin:
                continue

            # Title
            title_el = card.query_selector("h2 span")
            title = clean_text(title_el.inner_text()) if title_el else None

            # Selling price (whole + fraction)
            price_whole = card.query_selector("span.a-price-whole")
            price_frac  = card.query_selector("span.a-price-fraction")
            price_str = ""
            if price_whole:
                price_str = price_whole.inner_text().replace(",", "").replace(".", "")
                if price_frac:
                    price_str += "." + price_frac.inner_text()
            price = parse_price(price_str)

            # MRP (crossed-out price)
            mrp_el = card.query_selector("span.a-price.a-text-price span.a-offscreen")
            mrp = parse_price(mrp_el.inner_text()) if mrp_el else None

            # Discount percentage shown on card (e.g. "-42%")
            discount_badge = card.query_selector("span.a-letter-space + span")
            discount_pct = None
            if discount_badge:
                txt = discount_badge.inner_text()
                if "%" in txt:
                    try:
                        discount_pct = float(txt.replace("%", "").replace("-", "").strip())
                    except ValueError:
                        pass
            # Fallback: compute from price + MRP
            if discount_pct is None and price and mrp and mrp > price:
                discount_pct = round((mrp - price) / mrp * 100, 1)

            # Star rating
            rating_el = card.query_selector("span.a-icon-alt")
            rating = None
            if rating_el:
                try:
                    rating = float(rating_el.inner_text().split()[0])
                except (ValueError, IndexError):
                    pass

            # Review count
            review_count_el = card.query_selector("span.a-size-base.s-underline-text")
            review_count = None
            if review_count_el:
                try:
                    review_count = int(
                        review_count_el.inner_text().replace(",", "").strip()
                    )
                except ValueError:
                    pass

            # Product URL
            link_el = card.query_selector("a.a-link-normal.s-no-outline")
            url = None
            if link_el:
                href = link_el.get_attribute("href")
                if href:
                    url = "https://www.amazon.in" + href if href.startswith("/") else href

            # Product image
            img_el = card.query_selector("img.s-image")
            image_url = img_el.get_attribute("src") if img_el else None

            results.append({
                "id": asin,
                "asin": asin,
                "brand": brand.lower(),
                "title": title,
                "price": price,
                "mrp": mrp,
                "discount_pct": discount_pct,
                "rating": rating,
                "review_count": review_count,
                "url": url,
                "image_url": image_url,
                "scraped_at": datetime.utcnow().isoformat() + "Z",
            })

        except Exception as e:
            print(f"  [warn] skipped card: {e}")
            continue

    return results


# ---------------------------------------------------------------------------
# Scrape: Product reviews page
# ---------------------------------------------------------------------------

def extract_reviews(page, brand: str, asin: str) -> list[dict]:
    """
    Scrape reviews from amazon.in/product-reviews/{ASIN}.
    Collects: review_id, star, title, body, date, verified, helpful_votes.
    """
    results = []

    review_els = page.query_selector_all('[data-hook="review"]')

    for rev in review_els:
        try:
            review_id = rev.get_attribute("id")  # e.g. "R2B3K4..."

            # Star rating
            star_el = rev.query_selector('span[data-hook="review-star-rating"] span.a-icon-alt')
            if not star_el:
                star_el = rev.query_selector('span[data-hook="cmps-review-star-rating"] span.a-icon-alt')
            star = None
            if star_el:
                try:
                    star = float(star_el.inner_text().split()[0])
                except (ValueError, IndexError):
                    pass

            # Review title
            title_el = rev.query_selector('a[data-hook="review-title"] span:not(.a-icon-alt)')
            if not title_el:
                title_el = rev.query_selector('span[data-hook="review-title"]')
            review_title = clean_text(title_el.inner_text()) if title_el else None

            # Review body
            body_el = rev.query_selector('span[data-hook="review-body"]')
            body = clean_text(body_el.inner_text()) if body_el else ""

            # Date (e.g. "Reviewed in India on 12 March 2024")
            date_el = rev.query_selector('span[data-hook="review-date"]')
            date_str = clean_text(date_el.inner_text()) if date_el else None

            # Verified purchase badge
            verified_el = rev.query_selector('span[data-hook="avp-badge"]')
            verified = verified_el is not None

            # Helpful votes
            helpful_el = rev.query_selector('span[data-hook="helpful-vote-statement"]')
            helpful_votes = None
            if helpful_el:
                txt = helpful_el.inner_text()
                try:
                    helpful_votes = int(txt.split()[0].replace(",", ""))
                except (ValueError, IndexError):
                    helpful_votes = 1  # "One person found this helpful"

            results.append({
                "id": f"{asin}_{review_id}",
                "review_id": review_id,
                "asin": asin,
                "brand": brand.lower(),
                "star": star,
                "review_title": review_title,
                "body": body,
                "date_str": date_str,
                "verified": verified,
                "helpful_votes": helpful_votes,
                "scraped_at": datetime.utcnow().isoformat() + "Z",
            })

        except Exception as e:
            print(f"  [warn] skipped review: {e}")
            continue

    return results


# ---------------------------------------------------------------------------
# Floating button injection (same approach as Twitter scraper)
# ---------------------------------------------------------------------------

FLOATING_BUTTON_JS = r"""
(function() {
    if (!window.__amzScraperState) {
        window.__amzScraperState = { saveRequested: false, quitRequested: false };
    }

    function requestSave() {
        window.__amzScraperState.saveRequested = true;
    }

    function requestQuit() {
        window.__amzScraperState.quitRequested = true;
    }

  function addBtn() {
    if (document.getElementById('amz-scraper-btn')) return;
    const btn = document.createElement('button');
    btn.id = 'amz-scraper-btn';
    btn.textContent = 'Save items';
    Object.assign(btn.style, {
      position: 'fixed',
      bottom: '16px',
      right: '16px',
      zIndex: '2147483647',
      padding: '10px 16px',
      fontSize: '14px',
      fontWeight: '600',
      borderRadius: '8px',
      border: '2px solid #ff9900',
      background: '#fff',
      color: '#111',
      cursor: 'pointer',
      boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
    });
    btn.addEventListener('click', () => {
      btn.textContent = 'Saving...';
            requestSave();
      setTimeout(() => { btn.textContent = 'Save items'; }, 800);
    });
    document.documentElement.appendChild(btn);
  }
  
  // Add keyboard shortcuts (s = save, q = quit)
  document.addEventListener('keydown', (e) => {
    // Only trigger if not typing in an input field
    const activeInfo = document.activeElement.tagName.toLowerCase();
    if (activeInfo === 'input' || activeInfo === 'textarea') return;

    if (e.key.toLowerCase() === 's') {
      const btn = document.getElementById('amz-scraper-btn');
      if (btn) btn.click();
    } else if (e.key.toLowerCase() === 'q') {
            requestQuit();
    }
  });

  try { addBtn(); } catch(e) {}
  window.addEventListener('DOMContentLoaded', addBtn);
})();
"""


# ---------------------------------------------------------------------------
# Main interactive loop
# ---------------------------------------------------------------------------

def run_search_mode(page, brand: str):
    """Scrape product listing pages. You scroll, press 's' to save."""
    out_path = DATA_DIR / f"{brand}_products.json"
    seen: set[str] = load_seen_ids(out_path)

    def scrape_now():
        print(">> [DEBUG] 's' pressed! Scraping current visible page...")
        batch = extract_search_results(page, brand)
        if not batch:
            print("  [hint] No product cards detected. Scroll more or ensure you're on Amazon search results.")
        new = [r for r in batch if r["id"] not in seen]
        for r in new:
            seen.add(r["id"])
        saved = append_json(out_path, new)
        print(
            f"  [{datetime.now().strftime('%H:%M:%S')}] "
            f"Found {len(batch)} cards on screen. Saved {saved} new products! Total unique: {len(seen)}"
        )

    page.add_init_script(FLOATING_BUTTON_JS)
    page.evaluate(FLOATING_BUTTON_JS)
    # We do NOT reload here, because it resets your scrolling position!

    print(f"\n[SEARCH MODE] Brand: {brand}")
    print(f"  Output: {out_path}")
    print("  Navigate to the Amazon search results for this brand.")
    print("  Scroll down to load more products, press 's' to save visible cards.")
    print("  Move to next page and repeat. Press 'q' to quit.\n")

    print("  Running initial capture on current page...")
    scrape_now()

    _interactive_loop(page, scrape_now)


def run_reviews_mode(page, brand: str, asin: str):
    """Scrape reviews for a specific ASIN. You scroll, press 's' to save."""
    out_path = DATA_DIR / f"{brand}_reviews.json"
    seen: set[str] = load_seen_ids(out_path)

    def scrape_now():
        print(">> [DEBUG] 's' pressed! Scraping current visible reviews...")
        batch = extract_reviews(page, brand, asin)
        if not batch:
            print("  [hint] No reviews detected. Ensure review blocks are visible and page finished loading.")
        new = [r for r in batch if r["id"] not in seen]
        for r in new:
            seen.add(r["id"])
        saved = append_json(out_path, new)
        print(
            f"  [{datetime.now().strftime('%H:%M:%S')}] "
            f"Found {len(batch)} reviews on screen. Saved {saved} new reviews for {asin}! Total unique: {len(seen)}"
        )

    page.add_init_script(FLOATING_BUTTON_JS)
    page.evaluate(FLOATING_BUTTON_JS)
    # We do NOT reload here, because it resets your scroll progress

    print(f"\n[REVIEWS MODE] Brand: {brand}  ASIN: {asin}")
    print(f"  Output: {out_path}")
    print(f"  Navigate to: https://www.amazon.in/product-reviews/{asin}")
    print("  Scroll down to load reviews, press 's' to save visible reviews.")
    print("  Go to next review page and repeat. Press 'q' to quit.\n")

    print("  Running initial capture on current page...")
    scrape_now()

    _interactive_loop(page, scrape_now)


def _interactive_loop(page, scrape_now_fn):
    """Non-blocking key loop. 's' = save, 'q' = quit."""
    running = True
    use_windows_keys = platform.system() == "Windows" and msvcrt is not None

    # Reset browser signal flags at loop start.
    try:
        page.evaluate(
            """
            () => {
              if (!window.__amzScraperState) {
                window.__amzScraperState = { saveRequested: false, quitRequested: false };
              } else {
                window.__amzScraperState.saveRequested = false;
                window.__amzScraperState.quitRequested = false;
              }
            }
            """
        )
    except Exception:
        pass

    while running:
        if use_windows_keys:
            assert msvcrt is not None
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                try:
                    ch = ch.decode().lower()
                except Exception:
                    ch = ""
                if ch == "s":
                    scrape_now_fn()
                elif ch == "q":
                    running = False
        elif select is not None:
            dr, _, _ = select.select([sys.stdin], [], [], 0.1)
            if dr:
                ch = sys.stdin.read(1).lower()
                if ch == "s":
                    scrape_now_fn()
                elif ch == "q":
                    running = False

        # Poll browser-side save/quit signals set by JS button/shortcuts.
        try:
            browser_state = page.evaluate(
                """
                () => {
                  if (!window.__amzScraperState) {
                    return { saveRequested: false, quitRequested: false };
                  }
                  return {
                    saveRequested: Boolean(window.__amzScraperState.saveRequested),
                    quitRequested: Boolean(window.__amzScraperState.quitRequested),
                  };
                }
                """
            )
            if browser_state.get("saveRequested"):
                scrape_now_fn()
                page.evaluate(
                    "() => { if (window.__amzScraperState) window.__amzScraperState.saveRequested = false; }"
                )
            if browser_state.get("quitRequested"):
                running = False
                page.evaluate(
                    "() => { if (window.__amzScraperState) window.__amzScraperState.quitRequested = false; }"
                )
        except Exception:
            pass

        try:
            page.wait_for_timeout(100)
        except Exception:
            break

    print("\nDetaching. Chrome stays open. Bye!")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Amazon India scraper via CDP")
    parser.add_argument(
        "--mode",
        choices=["search", "reviews"],
        required=True,
        help="'search' = scrape product listing page; 'reviews' = scrape review pages",
    )
    parser.add_argument(
        "--brand",
        required=True,
        help="Brand slug (e.g. safari, skybags, american_tourister, vip)",
    )
    parser.add_argument(
        "--asin",
        default=None,
        help="ASIN of the product (required for --mode reviews)",
    )
    args = parser.parse_args()

    if args.mode == "reviews" and not args.asin:
        parser.error("--asin is required when --mode is reviews")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError(
                "No browser contexts found. "
                "Is Chrome running with --remote-debugging-port=9222?"
            )
        context = contexts[0]
        page = context.new_page()

        if args.mode == "search":
            # Auto-navigate to search results for convenience
            search_url = f"https://www.amazon.in/s?k={args.brand.replace('_', '+')}+luggage&i=luggage"
            print(f"Navigating to: {search_url}")
            page.goto(search_url, wait_until="domcontentloaded")
            found = wait_for_any_selector(
                page,
                [
                    'div[data-component-type="s-search-result"]',
                    'div.s-main-slot',
                ],
                timeout=15_000,
            )
            if not found:
                print("[warn] Search cards not detected yet. You can still scroll and press 's' to try saving.")
            run_search_mode(page, args.brand)

        elif args.mode == "reviews":
            reviews_url = f"https://www.amazon.in/product-reviews/{args.asin}?sortBy=recent&reviewerType=all_reviews"
            print(f"Navigating to: {reviews_url}")
            page.goto(reviews_url, wait_until="domcontentloaded")
            found = wait_for_any_selector(
                page,
                [
                    'div[data-hook="review"]',
                    '#cm_cr-review_list',
                    '#cm_cr-review_list_slot',
                ],
                timeout=15_000,
            )
            if not found:
                print("[warn] Review cards not detected yet. Scroll or wait for the page to load, then press 's'.")
            run_reviews_mode(page, args.brand, args.asin)


if __name__ == "__main__":
    main()