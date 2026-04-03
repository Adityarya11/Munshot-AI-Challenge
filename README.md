# 🧳 Moonshot Luggage Intelligence Dashboard

An end-to-end competitive intelligence pipeline and interactive dashboard analyzing luggage brands on Amazon India. This system bypasses aggressive bot-protection to scrape raw marketplace data, processes unstructured reviews into structured sentiment and aspect themes, and uses an LLM to generate decision-ready business insights.

## 🏗️ Architecture & Tech Stack

- **Data Acquisition (Scraper):** Playwright (CDP mode) + Python. Connects to a live Chrome instance to evade bot detection.
- **Storage:** SQLite (`data/luggage.db`) ingested via `INSERT OR REPLACE` for idempotent updates.
- **NLP Pipeline:** `VADER` for lightweight, deterministic sentence-level sentiment analysis. Rule-based aspect extraction for high-accuracy theme tagging (wheels, zippers, durability).
- **LLM Insights Agent:** Google Gemini (auto-selects available model). If API quota is exhausted, script generates deterministic fallback insights so the dashboard still works.
- **Frontend:** Streamlit + Plotly for a responsive, filterable, dynamic UI.

## ⚙️ Prerequisites

This project is optimized for a Linux environment (like WSL on Windows).

1.  **Python 3.11+**
2.  **uv** (Fast Python package installer and resolver)
3.  **Google Chrome** installed on the host machine.
4.  **Gemini API Key** (Free tier from Google AI Studio).

## 🚀 Installation & Setup

1. Clone the repository and initialize the environment:

   ```bash
   uv sync
   uv run playwright install chromium
   ```

2. Create a `.env` file in the root directory:

   ```env
   GEMINI_API_KEY=your_actual_api_key_here
   ```

3. Launch Chrome with remote debugging enabled (Required for the scraper):
   ```bash
   # Windows (Run in PowerShell/CMD, not WSL)
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug"
   ```

## 🔄 Execution Workflow

The pipeline consists of four distinct phases, executed sequentially:

### Phase 1: Data Acquisition

#### Step A: Start Chrome in Debug Mode

Required for Playwright CDP connection.

```bash
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug"
```

#### Step B: Scrape Product Listings Per Brand

Each command opens the search flow for one brand and writes to `data/raw/{brand}_products.json`.

```bash
uv run python scraper.py --mode search --brand safari
uv run python scraper.py --mode search --brand skybags
uv run python scraper.py --mode search --brand american_tourister
uv run python scraper.py --mode search --brand vip
uv run python scraper.py --mode search --brand aristocrat
uv run python scraper.py --mode search --brand nasher_miles
```

#### Step C: Bulk Review Scraping (Recommended)

This uses `bulk_reviews.py` to:

- read all ASINs from `*_products.json`
- save grouped ASINs to `data/asins_by_brand.json`
- visit review pages automatically
- save reviews to `data/raw/{brand}_reviews.json`

```bash
uv run python bulk_reviews.py --brands safari skybags american_tourister vip aristocrat nasher_miles --max-asins-per-brand 10 --pages-per-asin 2
```

Useful variants:

```bash
# reuse existing ASIN map instead of rebuilding from product files
uv run python bulk_reviews.py --brands safari skybags american_tourister vip aristocrat nasher_miles --max-asins-per-brand 10 --pages-per-asin 2 --use-existing-asins

# quick smoke test
uv run python bulk_reviews.py --brands safari --max-asins-per-brand 1 --pages-per-asin 1
```

#### Optional: Manual Review Scraping For One ASIN

```bash
uv run python scraper.py --mode reviews --brand safari --asin B097JK62G2
```

Controls while interactive scraper is running:

- Press `s` in the browser page (or terminal) to save currently visible cards/reviews.
- Press `q` in the browser page (or terminal) to quit the scraper loop.

### Phase 2: Database Ingestion

Load all raw JSON files into SQLite (`products`, `reviews`, `themes`, `insights` schema pre-created).

```bash
uv run python db.py --ingest all
```

### Phase 3: AI Processing & Agent Insights

Run NLP scoring first, then generate strategic insights.

```bash
uv run python process.py
uv run python insights.py
```

`process.py` does:

- review sentiment scoring (`sentiment_score`)
- aspect-level theme extraction (`themes` table)

`insights.py` does:

- DB aggregation by brand
- Gemini insight generation when API is available
- fallback deterministic insights when API quota/model is unavailable

### Phase 4: Visualization

Launch the Streamlit dashboard.

```bash
uv run streamlit run dashboard.py
```

### Coverage Check (Assignment Readiness)

Check how many products and reviews were collected per brand.

```bash
uv run python -c "import sqlite3; c=sqlite3.connect('data/luggage.db'); cur=c.cursor(); brands=[r[0] for r in cur.execute('select distinct brand from products order by brand')]; print('coverage:'); [print(f'{b}: products={cur.execute(\"select count(distinct asin) from products where brand=?\",(b,)).fetchone()[0]}, reviews={cur.execute(\"select count(*) from reviews where brand=?\",(b,)).fetchone()[0]}') for b in brands]; c.close()"
```

## 🧠 Sentiment & Theme Methodology

To balance performance and accuracy over thousands of rows, the NLP pipeline avoids heavy zero-shot transformer models in favor of a deterministic, multi-layered approach:

1.  **Sentence-Level VADER:** Reviews are split into sentences. VADER calculates a compound score (-1 to +1) for the overall review text.
2.  **Targeted Aspect Extraction:** A predefined list of high-value luggage components (wheels, zipper, handle, material, size, durability) acts as a heuristic filter.
3.  **Contextual Polarity:** If an aspect is mentioned, the specific sentence containing that aspect is scored. This allows the system to correctly parse complex reviews (e.g., _"The wheels are amazing, but the zipper broke immediately"_ registers positive for wheels and negative for zipper).
4.  **LLM Synthesis:** Aggregated sentiment and theme counts are fed to Gemini to identify macro-trends (e.g., "Brand X discounts heavily but suffers from zipper complaints").
