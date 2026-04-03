import importlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

DB_PATH = Path("data/luggage.db")

try:
    genai: Any = importlib.import_module("google.generativeai")
except ModuleNotFoundError:
    genai = None


def _extract_json_array(text: str) -> list[dict]:
    if not text:
        return []

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []


def _pick_model_name() -> str:
    if not genai:
        return ""
    preferred = [
        "models/gemini-2.0-flash",
        "models/gemini-2.0-flash-001",
        "models/gemini-2.5-flash",
    ]
    available = {
        m.name
        for m in genai.list_models()
        if "generateContent" in getattr(m, "supported_generation_methods", [])
    }
    for name in preferred:
        if name in available:
            return name
    return next(iter(available), "models/gemini-2.0-flash")


def _safe_ratio(num: float, den: float) -> float:
    return (num / den) if den else 0.0


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def get_aggregated_data() -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            p.brand,
            COUNT(DISTINCT p.asin) AS product_count,
            COUNT(r.id) AS review_count,
            AVG(p.price) AS avg_price,
            AVG(p.discount_pct) AS avg_discount,
            AVG(p.rating) AS avg_rating,
            AVG(r.sentiment_score) AS avg_sentiment,
            AVG(CASE WHEN r.verified = 1 THEN 1.0 ELSE 0.0 END) AS verified_ratio,
            AVG(COALESCE(r.helpful_votes, 0)) AS avg_helpful_votes
        FROM products p
        LEFT JOIN reviews r ON p.asin = r.asin
        GROUP BY p.brand
        """
    )
    brand_metrics = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT brand, theme_keyword, polarity, COUNT(*) AS mention_count
        FROM themes
        GROUP BY brand, theme_keyword, polarity
        ORDER BY mention_count DESC
        """
    )
    theme_metrics = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return {
        "brand_metrics": brand_metrics,
        "theme_metrics": theme_metrics,
    }


def _rule_based_insights(data_context: dict) -> list[dict]:
    brands = data_context.get("brand_metrics", [])
    themes = data_context.get("theme_metrics", [])
    if not brands:
        return []

    brand_map = {b["brand"]: dict(b) for b in brands}

    neg_theme_totals: dict[str, int] = {}
    pos_theme_totals: dict[str, int] = {}
    top_negative_theme: dict[str, tuple[str, int]] = {}

    for row in themes:
        brand = row["brand"]
        polarity = row["polarity"]
        theme = row["theme_keyword"]
        cnt = int(row["mention_count"] or 0)
        if polarity == "negative":
            neg_theme_totals[brand] = neg_theme_totals.get(brand, 0) + cnt
            cur = top_negative_theme.get(brand)
            if not cur or cnt > cur[1]:
                top_negative_theme[brand] = (theme, cnt)
        elif polarity == "positive":
            pos_theme_totals[brand] = pos_theme_totals.get(brand, 0) + cnt

    brand_names = list(brand_map.keys())
    avg_sentiments = [float(brand_map[b].get("avg_sentiment") or 0.0) for b in brand_names]
    avg_ratings = [float(brand_map[b].get("avg_rating") or 0.0) for b in brand_names]
    avg_prices = [float(brand_map[b].get("avg_price") or 0.0) for b in brand_names]
    avg_discounts = [float(brand_map[b].get("avg_discount") or 0.0) for b in brand_names]

    sent_norm = _minmax(avg_sentiments)
    rating_norm = _minmax(avg_ratings)
    price_norm = _minmax(avg_prices)
    discount_norm = _minmax(avg_discounts)

    derived = {}
    for idx, brand in enumerate(brand_names):
        bm = brand_map[brand]
        review_count = float(bm.get("review_count") or 0.0)
        verified_ratio = float(bm.get("verified_ratio") or 0.0)
        helpful_ratio = min(float(bm.get("avg_helpful_votes") or 0.0) / 5.0, 1.0)
        trust_score = 0.7 * verified_ratio + 0.3 * helpful_ratio

        neg_density = _safe_ratio(float(neg_theme_totals.get(brand, 0)), review_count)
        pos_density = _safe_ratio(float(pos_theme_totals.get(brand, 0)), review_count)

        value_score = 100 * (
            0.35 * sent_norm[idx]
            + 0.25 * rating_norm[idx]
            + 0.20 * discount_norm[idx]
            + 0.20 * (1 - price_norm[idx])
        )

        strategy_score = 100 * (
            0.40 * sent_norm[idx]
            + 0.25 * rating_norm[idx]
            + 0.20 * trust_score
            + 0.15 * (1 - min(neg_density, 1.0))
        )

        derived[brand] = {
            "value_score": value_score,
            "strategy_score": strategy_score,
            "neg_density": neg_density,
            "pos_density": pos_density,
            "avg_price": float(bm.get("avg_price") or 0.0),
            "avg_discount": float(bm.get("avg_discount") or 0.0),
            "avg_sentiment": float(bm.get("avg_sentiment") or 0.0),
            "avg_rating": float(bm.get("avg_rating") or 0.0),
            "review_count": int(bm.get("review_count") or 0),
            "product_count": int(bm.get("product_count") or 0),
        }

    leader = max(derived.items(), key=lambda x: x[1]["strategy_score"])
    value_leader = max(derived.items(), key=lambda x: x[1]["value_score"])
    risk_brand = max(derived.items(), key=lambda x: x[1]["neg_density"])
    expensive_brand = max(derived.items(), key=lambda x: x[1]["avg_price"])
    discount_brand = max(derived.items(), key=lambda x: x[1]["avg_discount"])
    low_coverage = min(derived.items(), key=lambda x: x[1]["review_count"])

    expensive_is_underperforming = expensive_brand[1]["avg_sentiment"] < (
        sum(v["avg_sentiment"] for v in derived.values()) / max(len(derived), 1)
    )

    insights: list[dict] = [
        {
            "brand": leader[0],
            "category": "Positioning",
            "insight_text": (
                f"{leader[0].title()} leads the competitive score with strong sentiment and trust signals. "
                f"Use this brand as the benchmark for quality and customer experience playbooks."
            ),
        },
        {
            "brand": value_leader[0],
            "category": "Value",
            "insight_text": (
                f"{value_leader[0].title()} is the best value-for-money player (price, discount, rating, sentiment combined). "
                "This positioning can be amplified for conversion-focused campaigns."
            ),
        },
        {
            "brand": risk_brand[0],
            "category": "Quality",
            "insight_text": (
                f"{risk_brand[0].title()} has the highest density of negative aspect mentions per review. "
                "Prioritize defect root-cause fixes before scaling paid acquisition."
            ),
        },
        {
            "brand": discount_brand[0],
            "category": "Pricing",
            "insight_text": (
                f"{discount_brand[0].title()} runs the deepest discounting, but discount-led growth should be validated "
                "against long-term sentiment and repeat purchase quality."
            ),
        },
    ]

    if expensive_is_underperforming:
        insights.append(
            {
                "brand": expensive_brand[0],
                "category": "Pricing",
                "insight_text": (
                    f"{expensive_brand[0].title()} is priced at a premium but sentiment trails portfolio average. "
                    "Either improve product quality cues or revisit price architecture."
                ),
            }
        )

    if top_negative_theme:
        brand, (theme, cnt) = max(top_negative_theme.items(), key=lambda x: x[1][1])
        insights.append(
            {
                "brand": brand,
                "category": "Customer Experience",
                "insight_text": (
                    f"The single biggest complaint cluster is {theme} for {brand.title()} ({cnt} mentions). "
                    "Fixing this one issue can unlock a disproportionate sentiment lift."
                ),
            }
        )

    insights.append(
        {
            "brand": low_coverage[0],
            "category": "Data Quality",
            "insight_text": (
                f"{low_coverage[0].title()} has the lowest review coverage ({low_coverage[1]['review_count']} reviews). "
                "Collect more review samples before making major strategic decisions for this brand."
            ),
        }
    )

    return insights[:8]


def _maybe_refine_with_llm(base_insights: list[dict], data_context: dict) -> list[dict]:
    """Optionally refine wording with LLM when INSIGHTS_USE_LLM=1."""
    if not base_insights:
        return base_insights

    use_llm = os.environ.get("INSIGHTS_USE_LLM", "0") == "1"
    api_key = os.environ.get("GEMINI_API_KEY")
    if not use_llm or not api_key or not genai:
        return base_insights

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(_pick_model_name())
        prompt = f"""
Rewrite the following insights for executive clarity.
Rules:
- preserve the exact analytical meaning
- one concise sentence per insight
- no hype language
- return only JSON list with fields: brand, category, insight_text

Input insights:
{json.dumps(base_insights, indent=2)}

Data context:
{json.dumps(data_context, indent=2)}
"""
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 1200,
                "response_mime_type": "application/json",
            },
        )
        parsed = _extract_json_array(getattr(response, "text", ""))
        refined = [
            item
            for item in parsed
            if isinstance(item, dict)
            and item.get("brand")
            and item.get("category")
            and item.get("insight_text")
        ]
        return refined[: len(base_insights)] if refined else base_insights
    except Exception as exc:
        print(f"[warn] LLM refinement skipped: {exc}")
        return base_insights


def generate_insights() -> None:
    load_dotenv()

    data_context = get_aggregated_data()
    if not data_context["brand_metrics"]:
        print("[ERROR] No data in DB to analyze.")
        return

    base_insights = _rule_based_insights(data_context)
    insights = _maybe_refine_with_llm(base_insights, data_context)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM insights")

    for item in insights:
        cursor.execute(
            """
            INSERT INTO insights (brand, insight_text, category, generated_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (item.get("brand", "General"), item["insight_text"], item["category"]),
        )

    conn.commit()
    conn.close()
    print(f"[INSIGHTS] Successfully generated and saved {len(insights)} insights.")


if __name__ == "__main__":
    generate_insights()
