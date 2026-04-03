import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = Path("data/luggage.db")

st.set_page_config(page_title="Luggage Intelligence Dashboard", layout="wide")


@st.cache_data(ttl=60)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(DB_PATH)
    products_df = pd.read_sql("SELECT * FROM products", conn)
    reviews_df = pd.read_sql("SELECT * FROM reviews", conn)
    themes_df = pd.read_sql("SELECT * FROM themes", conn)
    insights_df = pd.read_sql("SELECT * FROM insights", conn)
    conn.close()
    return products_df, reviews_df, themes_df, insights_df


def minmax(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype=float)
    lo, hi = series.min(), series.max()
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series([0.5] * len(series), index=series.index)
    return (series - lo) / (hi - lo)


def build_brand_table(df_prod: pd.DataFrame, df_rev: pd.DataFrame, df_themes: pd.DataFrame) -> pd.DataFrame:
    prod = (
        df_prod.groupby("brand", dropna=False)
        .agg(
            product_count=("asin", pd.Series.nunique),
            avg_price=("price", "mean"),
            avg_discount=("discount_pct", "mean"),
            avg_rating=("rating", "mean"),
        )
        .reset_index()
    )

    if df_rev.empty:
        rev = pd.DataFrame(
            {
                "brand": prod["brand"],
                "review_count": 0,
                "avg_sentiment": 0.0,
                "verified_ratio": 0.0,
                "avg_helpful_votes": 0.0,
            }
        )
    else:
        rev = (
            df_rev.assign(
                verified_num=df_rev["verified"].fillna(False).astype(int),
                helpful_num=df_rev["helpful_votes"].fillna(0),
            )
            .groupby("brand", dropna=False)
            .agg(
                review_count=("id", "count"),
                avg_sentiment=("sentiment_score", "mean"),
                verified_ratio=("verified_num", "mean"),
                avg_helpful_votes=("helpful_num", "mean"),
            )
            .reset_index()
        )

    if df_themes.empty:
        theme_rollup = pd.DataFrame(
            {
                "brand": prod["brand"],
                "negative_mentions": 0,
                "positive_mentions": 0,
            }
        )
    else:
        theme_rollup = (
            df_themes.assign(
                negative=(df_themes["polarity"] == "negative").astype(int),
                positive=(df_themes["polarity"] == "positive").astype(int),
            )
            .groupby("brand", dropna=False)
            .agg(
                negative_mentions=("negative", "sum"),
                positive_mentions=("positive", "sum"),
            )
            .reset_index()
        )

    brand = prod.merge(rev, on="brand", how="left").merge(theme_rollup, on="brand", how="left")
    brand = brand.fillna(
        {
            "review_count": 0,
            "avg_sentiment": 0,
            "verified_ratio": 0,
            "avg_helpful_votes": 0,
            "negative_mentions": 0,
            "positive_mentions": 0,
        }
    )

    brand["neg_density"] = brand["negative_mentions"] / brand["review_count"].replace(0, pd.NA)
    brand["pos_density"] = brand["positive_mentions"] / brand["review_count"].replace(0, pd.NA)
    brand[["neg_density", "pos_density"]] = brand[["neg_density", "pos_density"]].fillna(0)

    sent_norm = minmax(brand["avg_sentiment"])
    rating_norm = minmax(brand["avg_rating"].fillna(0))
    discount_norm = minmax(brand["avg_discount"].fillna(0))
    price_norm = minmax(brand["avg_price"].fillna(0))
    trust_score = 0.7 * brand["verified_ratio"].clip(0, 1) + 0.3 * (brand["avg_helpful_votes"] / 5).clip(0, 1)

    brand["value_score"] = (
        100
        * (
            0.35 * sent_norm
            + 0.25 * rating_norm
            + 0.20 * discount_norm
            + 0.20 * (1 - price_norm)
        )
    ).round(1)

    brand["strategy_score"] = (
        100
        * (
            0.40 * sent_norm
            + 0.25 * rating_norm
            + 0.20 * trust_score
            + 0.15 * (1 - brand["neg_density"].clip(0, 1))
        )
    ).round(1)

    brand["risk_score"] = (100 * (0.6 * brand["neg_density"].clip(0, 1) + 0.4 * (1 - sent_norm))).round(1)
    brand = brand.sort_values("strategy_score", ascending=False)
    return brand


def top_theme_table(df_themes: pd.DataFrame, polarity: str, top_n: int = 8) -> pd.DataFrame:
    if df_themes.empty:
        return pd.DataFrame(columns=["brand", "theme_keyword", "mentions"])

    subset = df_themes[df_themes["polarity"] == polarity]
    if subset.empty:
        return pd.DataFrame(columns=["brand", "theme_keyword", "mentions"])

    table = (
        subset.groupby(["brand", "theme_keyword"], dropna=False)
        .size()
        .reset_index(name="mentions")
        .sort_values(["mentions", "brand"], ascending=[False, True])
        .head(top_n)
    )
    return table


try:
    df_prod, df_rev, df_themes, df_insights = load_data()
except Exception as exc:
    st.error(f"Database error: {exc}. Ensure scraping and ingestion are complete.")
    st.stop()

if df_prod.empty:
    st.warning("No product data found. Run scraping and ingestion first.")
    st.stop()

st.sidebar.title("Filters")
brands_available = sorted(df_prod["brand"].dropna().unique().tolist())
brands = st.sidebar.multiselect("Brands", options=brands_available, default=brands_available)
min_rating = st.sidebar.slider("Minimum product rating", 1.0, 5.0, 1.0, 0.1)
min_reviews_brand = st.sidebar.slider("Minimum reviews per brand", 0, 300, 0, 5)

filtered_prod = df_prod[df_prod["brand"].isin(brands)].copy()
filtered_prod = filtered_prod[filtered_prod["rating"].fillna(0) >= min_rating]
filtered_rev = df_rev[df_rev["asin"].isin(filtered_prod["asin"])].copy()
filtered_themes = df_themes[df_themes["asin"].isin(filtered_prod["asin"])].copy()

brand_table = build_brand_table(filtered_prod, filtered_rev, filtered_themes)
brand_table = brand_table[brand_table["review_count"] >= min_reviews_brand]

if brand_table.empty:
    st.warning("No brands match current filters. Relax filters to continue.")
    st.stop()

st.title("Luggage Competitive Intelligence")
st.caption("Decision-focused dashboard: leadership, value, risk, and execution priorities.")

tabs = st.tabs(["Executive Brief", "Competitive Matrix", "Theme Intelligence", "Product Opportunities"])

with tabs[0]:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Brands analyzed", int(brand_table["brand"].nunique()))
    c2.metric("Products analyzed", int(filtered_prod["asin"].nunique()))
    c3.metric("Reviews analyzed", int(len(filtered_rev)))
    c4.metric("Portfolio sentiment", f"{filtered_rev['sentiment_score'].mean():.2f}" if not filtered_rev.empty else "N/A")

    leader = brand_table.iloc[0]
    value_leader = brand_table.sort_values("value_score", ascending=False).iloc[0]
    highest_risk = brand_table.sort_values("risk_score", ascending=False).iloc[0]

    k1, k2, k3 = st.columns(3)
    k1.info(
        f"Strategic Leader: {leader['brand'].title()}\n\n"
        f"Strategy Score: {leader['strategy_score']} | Sentiment: {leader['avg_sentiment']:.2f}"
    )
    k2.success(
        f"Value Leader: {value_leader['brand'].title()}\n\n"
        f"Value Score: {value_leader['value_score']} | Avg Discount: {value_leader['avg_discount']:.1f}%"
    )
    k3.error(
        f"Highest Execution Risk: {highest_risk['brand'].title()}\n\n"
        f"Risk Score: {highest_risk['risk_score']} | Negative Density: {highest_risk['neg_density']:.2f}"
    )

    st.subheader("Action Priorities")
    action_rows = []
    for _, row in brand_table.sort_values("risk_score", ascending=False).iterrows():
        if row["risk_score"] >= 60:
            action = "Urgent quality intervention"
            reason = "High complaint density and weaker sentiment"
        elif row["value_score"] >= 65:
            action = "Scale value-led campaigns"
            reason = "Strong value score with healthy quality indicators"
        else:
            action = "Optimize assortment"
            reason = "Mid-tier profile; improve differentiation or coverage"
        action_rows.append(
            {
                "Brand": row["brand"].title(),
                "Action": action,
                "Why": reason,
                "Strategy Score": row["strategy_score"],
            }
        )
    st.dataframe(pd.DataFrame(action_rows), width="stretch")

    st.subheader("Insight Feed")
    if not df_insights.empty:
        feed = df_insights[df_insights["brand"].isin(brand_table["brand"]) | (df_insights["brand"] == "General")].copy()
        feed = feed[["category", "brand", "insight_text"]].rename(
            columns={"category": "Category", "brand": "Brand", "insight_text": "Insight"}
        )
        st.dataframe(feed, width="stretch")
    else:
        st.warning("No insights found. Run insights.py to generate strategic insights.")

with tabs[1]:
    st.subheader("Brand Scorecard")
    scorecard = brand_table[
        [
            "brand",
            "product_count",
            "review_count",
            "avg_price",
            "avg_discount",
            "avg_rating",
            "avg_sentiment",
            "value_score",
            "strategy_score",
            "risk_score",
        ]
    ].copy()
    scorecard.columns = [
        "Brand",
        "Products",
        "Reviews",
        "Avg Price",
        "Avg Discount %",
        "Avg Rating",
        "Avg Sentiment",
        "Value Score",
        "Strategy Score",
        "Risk Score",
    ]
    st.dataframe(scorecard, width="stretch")

    left, right = st.columns(2)

    with left:
        fig = px.scatter(
            brand_table,
            x="avg_price",
            y="avg_sentiment",
            color="brand",
            size="review_count",
            hover_name="brand",
            title="Price vs Sentiment (bubble size = review volume)",
        )
        fig.update_layout(legend_title_text="Brand")
        st.plotly_chart(fig, width="stretch")

    with right:
        fig2 = px.bar(
            brand_table.sort_values("strategy_score", ascending=True),
            x="strategy_score",
            y="brand",
            orientation="h",
            color="risk_score",
            color_continuous_scale="RdYlGn_r",
            title="Strategy Score Ranking (color = risk)",
        )
        st.plotly_chart(fig2, width="stretch")

with tabs[2]:
    st.subheader("Theme Signals")

    if filtered_themes.empty:
        st.info("No theme data yet. Run process.py after reviews ingestion.")
    else:
        polarity_counts = (
            filtered_themes.groupby(["brand", "polarity"], dropna=False)
            .size()
            .reset_index(name="mentions")
        )
        fig3 = px.bar(
            polarity_counts,
            x="brand",
            y="mentions",
            color="polarity",
            barmode="group",
            title="Positive vs Negative Theme Mentions by Brand",
        )
        st.plotly_chart(fig3, width="stretch")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("Top Negative Themes")
            st.dataframe(top_theme_table(filtered_themes, polarity="negative", top_n=12), width="stretch")
        with c2:
            st.markdown("Top Positive Themes")
            st.dataframe(top_theme_table(filtered_themes, polarity="positive", top_n=12), width="stretch")

        anomaly = brand_table[
            (brand_table["avg_rating"] >= 4.0) & (brand_table["neg_density"] >= brand_table["neg_density"].median())
        ][["brand", "avg_rating", "avg_sentiment", "neg_density", "review_count"]]
        st.markdown("Potential Hidden-Risk Anomalies")
        if anomaly.empty:
            st.write("No clear high-rating/high-complaint anomalies under current filters.")
        else:
            st.dataframe(anomaly.rename(columns={"brand": "Brand"}), width="stretch")

with tabs[3]:
    st.subheader("Product Drilldown")

    prod_view = filtered_prod[["brand", "asin", "title", "price", "discount_pct", "rating", "review_count"]].copy()
    prod_view["label"] = (
        prod_view["brand"].fillna("unknown").str.title()
        + " | "
        + prod_view["asin"].fillna("")
        + " | "
        + prod_view["title"].fillna("Untitled")
    )

    selected = st.selectbox("Select product", options=prod_view["label"].dropna().unique())
    chosen = prod_view[prod_view["label"] == selected].iloc[0]

    asin = chosen["asin"]
    prod_reviews = filtered_rev[filtered_rev["asin"] == asin].copy()
    prod_themes = filtered_themes[filtered_themes["asin"] == asin].copy()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Price", f"{chosen['price']:.0f}" if pd.notna(chosen["price"]) else "N/A")
    m2.metric("Discount %", f"{chosen['discount_pct']:.1f}" if pd.notna(chosen["discount_pct"]) else "N/A")
    m3.metric("Rating", f"{chosen['rating']:.2f}" if pd.notna(chosen["rating"]) else "N/A")
    m4.metric("Review rows", int(len(prod_reviews)))

    left, right = st.columns(2)
    with left:
        if not prod_reviews.empty:
            sent_fig = px.histogram(
                prod_reviews,
                x="sentiment_score",
                nbins=20,
                title="Sentiment Distribution for Selected Product",
            )
            st.plotly_chart(sent_fig, width="stretch")
        else:
            st.write("No reviews available for selected product under current filters.")

    with right:
        if not prod_themes.empty:
            theme_summary = (
                prod_themes.groupby(["theme_keyword", "polarity"], dropna=False)
                .size()
                .unstack(fill_value=0)
                .sort_values(by=list(prod_themes["polarity"].unique()), ascending=False)
            )
            st.dataframe(theme_summary, width="stretch")
        else:
            st.write("No extracted themes available for this product.")

    st.markdown("Review Snippets")
    if prod_reviews.empty:
        st.write("No review snippets to display.")
    else:
        snippets = prod_reviews[["sentiment_score", "review_title", "body", "verified", "helpful_votes"]].copy()
        snippets = snippets.sort_values("sentiment_score", ascending=True)
        st.dataframe(snippets.head(12), width="stretch")
