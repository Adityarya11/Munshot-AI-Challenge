import sqlite3
import importlib
from pathlib import Path

try:
    SentimentIntensityAnalyzer = importlib.import_module(
        "vaderSentiment.vaderSentiment"
    ).SentimentIntensityAnalyzer
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency: vaderSentiment. Run 'uv sync' and retry."
    ) from exc

DB_PATH = Path("data/luggage.db")
ASPECTS = ["wheel", "zipper", "handle", "material", "size", "durability", "price", "chain", "lock"]

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def process_reviews():
    analyzer = SentimentIntensityAnalyzer()
    conn = get_connection()
    cursor = conn.cursor()

    # Fetch reviews that haven't been scored yet
    cursor.execute("SELECT id, review_id, asin, brand, body FROM reviews WHERE sentiment_score IS NULL")
    rows = cursor.fetchall()
    
    if not rows:
        print("[PROCESS] No new reviews to process.")
        return

    print(f"[PROCESS] Analyzing {len(rows)} new reviews...")

    updates = []
    themes = []

    for row in rows:
        text = str(row["body"]).lower()
        if not text or text == "none":
            updates.append((0.0, row["id"]))
            continue

        # 1. Overall Sentiment
        score = analyzer.polarity_scores(text)
        compound = score['compound']
        updates.append((compound, row["id"]))

        # 2. Aspect/Theme Extraction (Bonus Points)
        for aspect in ASPECTS:
            if aspect in text:
                # Determine if they liked or hated this specific aspect
                # We split into sentences to get the sentiment of the specific mention
                sentences = text.split('.')
                for sentence in sentences:
                    if aspect in sentence:
                        sent_score = analyzer.polarity_scores(sentence)['compound']
                        polarity = "positive" if sent_score >= 0.05 else "negative" if sent_score <= -0.05 else "neutral"
                        
                        if polarity != "neutral":
                            themes.append((row["review_id"], row["asin"], row["brand"], aspect, polarity))
                        break # Only log the first mention per review to avoid spam

    # Batch update reviews
    cursor.executemany("UPDATE reviews SET sentiment_score = ? WHERE id = ?", updates)
    
    # Batch insert themes
    cursor.executemany("""
        INSERT INTO themes (review_id, asin, brand, theme_keyword, polarity)
        VALUES (?, ?, ?, ?, ?)
    """, themes)

    conn.commit()
    conn.close()
    print(f"[PROCESS] Successfully updated {len(updates)} reviews and extracted {len(themes)} aspect themes.")

if __name__ == "__main__":
    process_reviews()