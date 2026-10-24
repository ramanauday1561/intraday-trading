"""Open news ingestion (Google News RSS + publisher RSS), event taxonomy and materiality extraction."""
import re
import statistics
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

from . import sentiment
from .config import NEG_EARN, POS_EARN, TAXONOMY

UA = {"User-Agent": "Mozilla/5.0 (compatible; intraday-pwa/1.0)"}
GENERAL_FEEDS = [
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("LiveMint Markets", "https://www.livemint.com/rss/markets"),
]


def _parse_rss(xml_text, default_source):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = (src_el.text if src_el is not None and src_el.text else default_source)
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
        if title:
            items.append({"title": title, "source": source, "url": (it.findtext("link") or "").strip(), "ts": ts})
    return items


def fetch_feed(url, source):
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        return _parse_rss(r.text, source)
    except requests.RequestException:
        return []


def fetch_symbol_news(name, symbol):
    q = quote_plus(f'"{name}" OR {symbol} NSE when:2d')
    return fetch_feed(f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en", "Google News")


def classify(title):
    t = title.lower()
    for tier, event, prob, rx in TAXONOMY:
        if re.search(rx, t):
            return tier, event, prob
    return 3, "OTHER", 0.15


_ORDER_RX = re.compile(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh crore|billion|bn|million|mn)", re.I)


def order_value_cr(title):
    m = _ORDER_RX.search(title)
    if not m:
        return None
    v, unit = float(m.group(1).replace(",", "")), m.group(2).lower()
    return v * {"crore": 1, "cr": 1, "lakh crore": 1e5, "billion": 100, "bn": 100, "million": 0.1, "mn": 0.1}[unit]


def analyse_symbol(symbol, name, mcap_cr, items, now=None):
    """Aggregate headline-level signals into the fundamental pillar for one symbol."""
    now = now or datetime.now(timezone.utc)
    items = [i for i in items if now - i["ts"] <= timedelta(hours=36)]
    seen, uniq = set(), []
    for i in items:
        k = re.sub(r"\W+", "", i["title"].lower())[:60]
        if k not in seen:
            seen.add(k); uniq.append(i)
    uniq = uniq[:12]
    if not uniq:
        return {"score": 0.0, "items": [], "event": None, "tier1_earnings": False, "positive_catalyst": 0.0,
                "sue_proxy": 0.0, "divergence": 0.0, "engine": None}
    sent = sentiment.analyse([i["title"] for i in uniq])
    rows, contribs, by_source = [], [], {}
    sue = 0.0
    for i, s in zip(uniq, sent):
        tier, event, prob = classify(i["title"])
        val = order_value_cr(i["title"]) if event == "ORDER_WIN" else None
        ratio = (val / mcap_cr * 100) if val and mcap_cr else None
        mat = 1.0
        if event == "ORDER_WIN":
            mat = 0.0 if (ratio is None or ratio < 1.0) else min(1.0, ratio / 5.0)
        if event == "EARNINGS":
            t = i["title"].lower()
            sue = 1.0 if re.search(POS_EARN, t) else -1.0 if re.search(NEG_EARN, t) else 0.0
        c = s["score"] * prob * mat
        contribs.append(c)
        by_source.setdefault(i["source"], []).append(s["score"])
        rows.append({"title": i["title"], "source": i["source"], "url": i["url"], "published": i["ts"].isoformat(),
                     "sentiment": round(s["score"], 3), "tier": tier, "event": event,
                     "momentum_probability": prob, "materiality": round(mat, 2),
                     "order_value_cr": val, "mcap_ratio_pct": round(ratio, 2) if ratio else None})
    rows.sort(key=lambda r: (r["tier"], -abs(r["sentiment"]) * r["materiality"]))
    top = rows[0]
    score = max(-1.0, min(1.0, sum(contribs) / max(1.0, len(contribs) ** 0.5)))
    means = [statistics.mean(v) for v in by_source.values()]
    divergence = statistics.pstdev(means) if len(means) > 1 else 0.0
    pos_cat = max([r["sentiment"] * r["momentum_probability"] * r["materiality"] for r in rows] + [0.0])
    return {"score": round(score, 3), "items": rows[:6], "event": top["event"] if top["materiality"] else None,
            "tier1_earnings": any(r["event"] == "EARNINGS" for r in rows), "positive_catalyst": round(pos_cat, 3),
            "sue_proxy": sue, "divergence": round(divergence, 3), "engine": sent[0]["engine"]}
