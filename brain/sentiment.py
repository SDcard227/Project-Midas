import requests
from datetime import datetime, timedelta


def get_sentiment(ticker: str, api_key: str) -> dict:
    """
    Fetches news sentiment for a ticker from Finnhub.

    Returns:
        score: 0.0 to 1.0 — bullish percent (above 0.6 = positive, below 0.4 = negative)
        label: 'positive', 'neutral', or 'negative'
        buzz: relative news volume (higher = more coverage)
        article_count: number of articles in the past 7 days
    """
    from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date = datetime.now().strftime("%Y-%m-%d")

    # Pull raw news articles
    news_resp = requests.get(
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": ticker, "from": from_date, "to": to_date, "token": api_key},
        timeout=10,
    )
    articles = news_resp.json() if news_resp.ok else []

    # Pull sentiment score
    sentiment_resp = requests.get(
        "https://finnhub.io/api/v1/news-sentiment",
        params={"symbol": ticker, "token": api_key},
        timeout=10,
    )

    if not sentiment_resp.ok:
        return {"score": 0.5, "label": "neutral", "buzz": 0, "article_count": len(articles)}

    data = sentiment_resp.json()
    score = data.get("sentiment", {}).get("bullishPercent", 0.5)
    buzz = data.get("buzz", {}).get("buzz", 0)

    if score > 0.6:
        label = "positive"
    elif score < 0.4:
        label = "negative"
    else:
        label = "neutral"

    return {
        "score": round(score, 4),
        "label": label,
        "buzz": round(buzz, 4),
        "article_count": len(articles),
    }
