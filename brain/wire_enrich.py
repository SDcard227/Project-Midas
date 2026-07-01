"""
MIDAS — wire_enrich: keyless "whisper" cards from raw headlines.

When there's no ANTHROPIC_API_KEY we can't run the AI classifier, but we can still surface
the two things that actually matter on a wire card: WHICH ticker a headline is about, and
HOW confident we are (confidence = how many independent sources corroborate it). This pulls
a ticker (cashtag > parenthetical exchange tag > company-name dictionary), scores bull/bear
off a keyword lexicon, groups mentions by ticker, and emits rows shaped exactly like the AI
whispers so the Wire renders them with no frontend change.

It's deliberately transparent (a lexicon, not a model). The AI path is strictly better and
takes over the moment a key is set; this just means the ticker + confidence cards never go
fully dark without one.
"""
import re
import hashlib

# ── company / product keyword -> ticker. keys are lowercase, matched on word boundaries ──
TICKER_MAP = {
    "apple": "AAPL", "iphone": "AAPL", "ipad": "AAPL", "macbook": "AAPL",
    "microsoft": "MSFT", "xbox": "MSFT", "windows": "MSFT", "azure": "MSFT", "copilot": "MSFT",
    "nvidia": "NVDA", "geforce": "NVDA",
    "google": "GOOGL", "alphabet": "GOOGL", "youtube": "GOOGL", "gemini": "GOOGL",
    "amazon": "AMZN", "aws": "AMZN",
    "meta": "META", "facebook": "META", "instagram": "META", "whatsapp": "META",
    "tesla": "TSLA", "elon musk": "TSLA", "cybertruck": "TSLA",
    "netflix": "NFLX", "disney": "DIS", "warner bros": "WBD", "paramount": "PARA",
    "amd": "AMD", "intel": "INTC", "qualcomm": "QCOM", "broadcom": "AVGO", "micron": "MU",
    "palantir": "PLTR", "salesforce": "CRM", "oracle": "ORCL", "adobe": "ADBE",
    "ibm": "IBM", "cisco": "CSCO", "dell": "DELL", "hp inc": "HPQ", "super micro": "SMCI",
    "arm holdings": "ARM", "snowflake": "SNOW", "servicenow": "NOW", "uber": "UBER", "lyft": "LYFT",
    "airbnb": "ABNB", "doordash": "DASH", "shopify": "SHOP", "spotify": "SPOT", "snap": "SNAP",
    "pinterest": "PINS", "reddit": "RDDT", "roku": "ROKU", "zoom": "ZM", "block": "SQ",
    "paypal": "PYPL", "coinbase": "COIN", "robinhood": "HOOD", "sofi": "SOFI",
    "microstrategy": "MSTR", "marathon digital": "MARA", "riot platforms": "RIOT",
    "jpmorgan": "JPM", "jp morgan": "JPM", "bank of america": "BAC", "wells fargo": "WFC",
    "citigroup": "C", "goldman sachs": "GS", "morgan stanley": "MS", "visa": "V", "mastercard": "MA",
    "berkshire": "BRK.B", "blackrock": "BLK", "american express": "AXP", "charles schwab": "SCHW",
    "exxon": "XOM", "chevron": "CVX", "occidental": "OXY", "conocophillips": "COP",
    "shell": "SHEL", "bp": "BP", "halliburton": "HAL", "schlumberger": "SLB",
    "pfizer": "PFE", "moderna": "MRNA", "biontech": "BNTX", "johnson & johnson": "JNJ",
    "merck": "MRK", "eli lilly": "LLY", "abbvie": "ABBV", "unitedhealth": "UNH",
    "bristol myers": "BMY", "gilead": "GILD", "amgen": "AMGN", "novo nordisk": "NVO",
    "walmart": "WMT", "costco": "COST", "target": "TGT", "home depot": "HD", "lowe's": "LOW",
    "nike": "NKE", "starbucks": "SBUX", "mcdonald's": "MCD", "coca-cola": "KO", "pepsi": "PEP",
    "procter & gamble": "PG", "chipotle": "CMG", "gamestop": "GME", "amc": "AMC",
    "boeing": "BA", "caterpillar": "CAT", "general electric": "GE", "ge aerospace": "GE",
    "lockheed": "LMT", "raytheon": "RTX", "3m": "MMM", "honeywell": "HON", "deere": "DE",
    "ford": "F", "general motors": "GM", "gm ": "GM", "rivian": "RIVN", "lucid": "LCID",
    "nio": "NIO", "ferrari": "RACE", "carnival": "CCL", "delta air": "DAL", "united airlines": "UAL",
    "american airlines": "AAL", "southwest": "LUV", "ryanair": "RYAAY", "boeing 737": "BA",
    "bruker": "BRKR", "outlook therapeutics": "OTLK",
    "at&t": "T", "verizon": "VZ", "t-mobile": "TMUS", "comcast": "CMCSA",
    "wildcatting": "OXY",
}

# common all-caps tokens that are NOT tickers — stops cashtag / paren false positives
STOP_TICKERS = {
    "CEO", "CFO", "COO", "CTO", "USA", "US", "UK", "EU", "UN", "GDP", "CPI", "PPI", "FED",
    "SEC", "FDA", "FBI", "CIA", "IRS", "DOJ", "ETF", "IPO", "AI", "EV", "PC", "TV", "NYSE",
    "NASDAQ", "OTC", "AMEX", "Q1", "Q2", "Q3", "Q4", "YOY", "EPS", "M&A", "ESG", "NFT", "API",
    "USD", "EUR", "GBP", "OPEC", "NATO", "WHO", "IMF", "OK", "NEW", "CEOS", "AND", "THE", "FOR",
}

CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
# (NASDAQ: BRKR) / (NYSE:XOM) / (BRKR)
PAREN = re.compile(r"\((?:NYSE|NASDAQ|NYSEARCA|NYSE American|OTC|AMEX|CBOE)?\s*:?\s*([A-Z]{2,5})\)")

BULL_WORDS = {
    "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies", "gain", "gains",
    "beat", "beats", "rise", "rises", "record", "high", "highs", "upgrade", "upgraded",
    "overweight", "outperform", "buy", "boost", "boosts", "strong", "tops", "raise", "raises",
    "expand", "expands", "approval", "approved", "breakthrough", "bullish", "higher", "climb",
    "climbs", "spike", "spikes", "pop", "pops", "win", "wins", "growth", "profit", "profits",
    "surged", "soared", "jumped", "rallied", "gained", "rose", "rallying", "rebound", "rebounds",
}
BEAR_WORDS = {
    "plunge", "plunges", "drop", "drops", "fall", "falls", "slump", "slumps", "sink", "sinks",
    "miss", "misses", "cut", "cuts", "downgrade", "downgraded", "underweight", "sell", "weak",
    "loss", "losses", "warn", "warns", "warning", "fear", "fears", "slash", "slashes", "tumble",
    "tumbles", "crash", "crashes", "lawsuit", "probe", "recall", "recalls", "halt", "halts",
    "ban", "bans", "threat", "threatens", "decline", "declines", "slide", "slides", "bearish",
    "lower", "woes", "risk", "risks", "concern", "concerns", "layoff", "layoffs", "bankruptcy",
    "plunged", "dropped", "fell", "sank", "tumbled", "crashed", "slid", "sued", "fine", "fined",
}

# first match wins — order matters (specific before generic)
CATEGORY_RULES = [
    ("analyst", {"rating", "upgrade", "downgrade", "overweight", "underweight", "price target",
                 "maintains", "initiates", "reiterates", "analyst", "buy rating", "sell rating"}),
    ("regulatory", {"sec ", "lawsuit", "court", "judge", "regulat", "filing", "8-k", "10-k",
                    "antitrust", "subpoena", "settlement", "probe", "fine", "sued", "ftc", "doj"}),
    ("macro", {"fed", "rate", "inflation", "gdp", "jobs", "economy", "tariff", "trump", "recession",
               "cpi", "treasury", "yield", "powell", "election", "sanction", "opec", "war"}),
    ("earnings", {"earnings", "revenue", "guidance", "beats", "misses", "eps", "quarter", "profit"}),
    ("product", {"price", "prices", "launch", "unveil", "release", "product", "chip", "model",
                 "feature", "hikes", "console", "phone", "car", "app"}),
]

RELIABLE_SOURCES = {"Reuters", "Bloomberg", "SEC EDGAR", "CNBC", "WSJ", "AP", "Associated Press",
                    "Financial Times", "MarketWatch", "Barron's"}


def extract_ticker(title):
    """Best-effort ticker for a headline. Returns an uppercase symbol or None."""
    if not title:
        return None
    # 1) $CASHTAG (strongest)
    for m in CASHTAG.findall(title):
        sym = m.upper()
        if sym not in STOP_TICKERS:
            return sym
    # 2) parenthetical exchange tag: (NASDAQ: BRKR) / (BRKR)
    for m in PAREN.findall(title):
        if m not in STOP_TICKERS:
            return m
    # 3) company-name dictionary (word-boundary, longest key first so "bank of america" wins)
    low = " " + title.lower() + " "
    for name in _SORTED_NAMES:
        if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", low):
            return TICKER_MAP[name]
    return None


_SORTED_NAMES = sorted(TICKER_MAP.keys(), key=len, reverse=True)


def _direction(title):
    """(direction, strength 0-3) from the bull/bear lexicon."""
    toks = re.findall(r"[a-z']+", (title or "").lower())
    b = sum(1 for t in toks if t in BULL_WORDS)
    s = sum(1 for t in toks if t in BEAR_WORDS)
    if b > s:
        return "bullish", min(3, b - s)
    if s > b:
        return "bearish", min(3, s - b)
    return "neutral", 0


def _category(title):
    low = (title or "").lower()
    for cat, words in CATEGORY_RULES:
        if any(w in low for w in words):
            return cat
    return "signal"


def _stage(n_sources):
    if n_sources >= 5:
        return "hauler"
    if n_sources >= 3:
        return "swell"
    if n_sources >= 2:
        return "whisper"
    return "leak"


def _sid(ticker):
    return "kw:" + hashlib.sha1(ticker.encode("utf-8")).hexdigest()[:10]


def build_keyless_whispers(articles, limit=40):
    """
    Group raw articles by extracted ticker into whisper-shaped cards.
    Returns a list of dicts matching the AI whisper schema the Wire renders:
    id, ticker, direction, confidence, source_count, event_type, stage, mentions_log.
    """
    groups = {}
    for a in articles or []:
        ticker = extract_ticker(a.get("title"))
        if not ticker:
            continue
        g = groups.setdefault(ticker, {"mentions": [], "sources": set(), "bull": 0, "bear": 0,
                                       "reliable": False})
        g["mentions"].append({"title": a.get("title", ""), "source": a.get("source", ""),
                              "link": a.get("link", "")})
        g["sources"].add(a.get("source", ""))
        if a.get("source") in RELIABLE_SOURCES:
            g["reliable"] = True
        d, strength = _direction(a.get("title"))
        if d == "bullish":
            g["bull"] += 1 + strength
        elif d == "bearish":
            g["bear"] += 1 + strength

    rows = []
    for ticker, g in groups.items():
        n = len(g["sources"])
        # net sentiment across the ticker's headlines
        if g["bull"] > g["bear"]:
            direction = "bullish"
        elif g["bear"] > g["bull"]:
            direction = "bearish"
        else:
            direction = "neutral"
        sentiment_bonus = min(6, abs(g["bull"] - g["bear"]) * 2)
        # confidence = corroboration first (1 src ~34, 2 ~67, 3 ~90), nudged by clarity + source quality
        conf = min(93, 34 + (n - 1) * 30 + sentiment_bonus + (4 if g["reliable"] else 0))
        latest = g["mentions"][-1]
        rows.append({
            "id": _sid(ticker),
            "ticker": ticker,
            "direction": direction,
            "confidence": conf,
            "source_count": n,
            "event_type": _category(latest["title"]),
            "stage": _stage(n),
            "mentions_log": g["mentions"][-6:],   # keep the card payload small
            "title": latest["title"],
            "link": latest["link"],
            "keyless": True,
        })
    # strongest first (corroboration, then clarity); cap the payload
    rows.sort(key=lambda r: (r["source_count"], r["confidence"]), reverse=True)
    return rows[:limit]
