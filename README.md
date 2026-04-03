# Moonshot Luggage Intelligence

Competitive intelligence pipeline for Amazon India luggage brands. The project collects product and review data, builds a structured SQLite dataset, computes sentiment and theme signals, generates strategic insights, and serves an interactive dashboard.

## Stack

- Scraping: Playwright (CDP connection to a real Chrome session)
- Storage: SQLite (`data/luggage.db`)
- Processing: NLTK VADER + rule-based aspect extraction
- Insight generation: deterministic strategy logic with optional Gemini refinement
- Visualization: Streamlit + Plotly

## Requirements

- Python 3.11+
- `uv`
- Google Chrome
- Optional: Gemini API key in `.env`

Environment variable:

```env
GEMINI_API_KEY=your_api_key
```

## Command Reference

### Environment and browser

```bash
uv sync
```

Responsibility: install Python dependencies from project lock/config.

```bash
uv run playwright install chromium
```

Responsibility: install Playwright browser runtime required by scraping scripts.

```bash
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug"
```

Responsibility: start Chrome with remote debugging so `scraper.py` can attach via CDP.

### Data collection

```bash
uv run python scraper.py --mode search --brand <brand_name>
```

Responsibility: collect product listing cards for one brand.

Output: `data/raw/<brand_name>_products.json`

Example brands:

```bash
uv run python scraper.py --mode search --brand safari
uv run python scraper.py --mode search --brand skybags
uv run python scraper.py --mode search --brand american_tourister
uv run python scraper.py --mode search --brand vip
uv run python scraper.py --mode search --brand aristocrat
uv run python scraper.py --mode search --brand nasher_miles
```

```bash
uv run python bulk_reviews.py --brands safari skybags american_tourister vip aristocrat nasher_miles --max-asins-per-brand 10 --pages-per-asin 2
```

Responsibility: run batch review scraping across brands and ASINs.

Outputs:

- `data/asins_by_brand.json`
- `data/raw/<brand_name>_reviews.json`

Useful variants:

```bash
uv run python bulk_reviews.py --brands safari skybags american_tourister vip aristocrat nasher_miles --max-asins-per-brand 10 --pages-per-asin 2 --use-existing-asins
uv run python bulk_reviews.py --brands safari --max-asins-per-brand 1 --pages-per-asin 1
```

```bash
uv run python scraper.py --mode reviews --brand <brand_name> --asin <asin>
```

Responsibility: scrape reviews for one ASIN in interactive mode.

Controls:

- `s`: save current visible reviews/products
- `q`: quit current loop

### Database and analytics pipeline

```bash
uv run python db.py --ingest all
```

Responsibility: ingest raw JSON into SQLite tables.

```bash
uv run python process.py
```

Responsibility: compute sentiment scores and aspect themes from review text.

```bash
uv run python insights.py
```

Responsibility: generate strategic insights from aggregated product/review/theme metrics.

### Dashboard

```bash
uv run streamlit run dashboard.py
```

Responsibility: launch interactive dashboard for brand comparison and recommendations.

### Data coverage check

```bash
uv run python -c "import sqlite3; c=sqlite3.connect('data/luggage.db'); cur=c.cursor(); brands=[r[0] for r in cur.execute('select distinct brand from products order by brand')]; print('coverage:'); [print(f'{b}: products={cur.execute(\"select count(distinct asin) from products where brand=?\",(b,)).fetchone()[0]}, reviews={cur.execute(\"select count(*) from reviews where brand=?\",(b,)).fetchone()[0]}') for b in brands]; c.close()"
```

Responsibility: print products and reviews count by brand from the SQLite database.

## Data Flow Summary

1. `scraper.py` and `bulk_reviews.py` write raw JSON files into `data/raw/`
2. `db.py` loads raw files into `data/luggage.db`
3. `process.py` enriches data with sentiment and themes
4. `insights.py` writes strategic observations into `insights` table
5. `dashboard.py` reads processed tables and renders the Streamlit UI

## Notes

- Run the Chrome debug command from Windows PowerShell or CMD .
- If Gemini quota is unavailable, insight generation falls back to deterministic logic so the pipeline still completes.
