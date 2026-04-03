# USAGE:
#   Initialize the database and ingest scraped JSON files:
#   uv run python db.py --ingest safari
#   uv run python db.py --ingest all

import argparse
import json
import sqlite3
from pathlib import Path

DB_PATH = Path("data/luggage.db")
RAW_DIR = Path("data/raw")

def get_connection():
    """Returns a configured SQLite connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def setup_db():
    """Initializes the database schema."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Products Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            asin TEXT PRIMARY KEY,
            brand TEXT,
            title TEXT,
            price REAL,
            mrp REAL,
            discount_pct REAL,
            rating REAL,
            review_count INTEGER,
            url TEXT,
            image_url TEXT,
            scraped_at TEXT
        )
    ''')

    # 2. Reviews Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY,
            review_id TEXT,
            asin TEXT,
            brand TEXT,
            star REAL,
            review_title TEXT,
            body TEXT,
            date_str TEXT,
            verified BOOLEAN,
            helpful_votes INTEGER,
            sentiment_score REAL,  -- Added later by process.py
            scraped_at TEXT,
            FOREIGN KEY(asin) REFERENCES products(asin)
        )
    ''')

    # 3. Themes Table (For KeyBERT aspect extraction later)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS themes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT,
            asin TEXT,
            brand TEXT,
            theme_keyword TEXT,
            polarity TEXT,       -- 'positive' or 'negative'
            FOREIGN KEY(review_id) REFERENCES reviews(review_id)
        )
    ''')

    # 4. Insights Table (For the LLM Agent layer later)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT,
            insight_text TEXT,
            category TEXT,
            generated_at TEXT
        )
    ''')

    conn.commit()
    conn.close()
    print(f"[DB] Schema initialized at {DB_PATH}")

def ingest_products(brand: str):
    """Reads product JSON and upserts into the products table."""
    file_path = RAW_DIR / f"{brand}_products.json"
    if not file_path.exists():
        print(f"  [warn] No product data found for {brand} at {file_path}")
        return 0

    with open(file_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    conn = get_connection()
    cursor = conn.cursor()
    
    count = 0
    for r in records:
        cursor.execute('''
            INSERT OR REPLACE INTO products 
            (asin, brand, title, price, mrp, discount_pct, rating, review_count, url, image_url, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            r.get("asin"), r.get("brand"), r.get("title"), r.get("price"), 
            r.get("mrp"), r.get("discount_pct"), r.get("rating"), 
            r.get("review_count"), r.get("url"), r.get("image_url"), r.get("scraped_at")
        ))
        count += 1

    conn.commit()
    conn.close()
    return count

def ingest_reviews(brand: str):
    """Reads reviews JSON and upserts into the reviews table."""
    file_path = RAW_DIR / f"{brand}_reviews.json"
    if not file_path.exists():
        print(f"  [warn] No review data found for {brand} at {file_path}")
        return 0

    with open(file_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    conn = get_connection()
    cursor = conn.cursor()
    
    count = 0
    for r in records:
        cursor.execute('''
            INSERT OR REPLACE INTO reviews 
            (id, review_id, asin, brand, star, review_title, body, date_str, verified, helpful_votes, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            r.get("id"), r.get("review_id"), r.get("asin"), r.get("brand"), 
            r.get("star"), r.get("review_title"), r.get("body"), 
            r.get("date_str"), r.get("verified"), r.get("helpful_votes"), r.get("scraped_at")
        ))
        count += 1

    conn.commit()
    conn.close()
    return count

def main():
    parser = argparse.ArgumentParser(description="Ingest scraped JSON into SQLite.")
    parser.add_argument("--ingest", help="Brand name to ingest, or 'all'")
    args = parser.parse_args()

    setup_db()

    if args.ingest:
        brands = ["safari", "skybags", "american_tourister", "vip"] if args.ingest.lower() == "all" else [args.ingest]
        
        for brand in brands:
            print(f"\n[INGEST] Processing brand: {brand}")
            prod_count = ingest_products(brand)
            rev_count = ingest_reviews(brand)
            print(f"  -> Inserted/Updated {prod_count} products.")
            print(f"  -> Inserted/Updated {rev_count} reviews.")

if __name__ == "__main__":
    main()