import streamlit as st
import pandas as pd
from buyntiq import ui, data
from buyntiq.analytics import analyze, model_brief
from buyntiq.state import persistent_widget

# News retrieval and relevance rules are local to this page.
import re
import time
from html import escape
from urllib.parse import urlsplit

_NEWS_ALIASES = {
    "AMD": ["AMD", "Advanced Micro Devices"],
    "NVDA": ["Nvidia"], "NFLX": ["Netflix"],
    "AAPL": ["Apple"], "MSFT": ["Microsoft"], "TSLA": ["Tesla"],
    "AMZN": ["Amazon", "Amazon Web Services"],
    "GOOG": ["Google", "Alphabet"], "GOOGL": ["Google", "Alphabet"],
    "META": ["Meta Platforms", "Facebook", "Instagram", "WhatsApp"],
    "BRK-B": ["Berkshire Hathaway"], "BRK-A": ["Berkshire Hathaway"],
}


def _news_terms(symbol, name):
    cleaned = re.sub(r"\s*·.*$", "", str(name or ""))
    cleaned = re.sub(r",?\s+(?:Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|plc|Company|Co\.?)$", "", cleaned, flags=re.I).strip()
    names = [cleaned] + _NEWS_ALIASES.get(symbol, [])
    # Never treat a bare ticker or generic company word as an unambiguous name.
    return list(dict.fromkeys(n for n in names if len(n) >= 3 and (n.upper() != symbol or symbol in _NEWS_ALIASES) and n.lower() not in {"holdings", "group", "company", "limited"}))


def _news_match(text, symbol, names):
    text = str(text or "")
    for name in names:
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
            return name
    # Explicit ticker syntax avoids matching ordinary words such as ON, IT or CAT.
    ticker = re.escape(symbol)
    if re.search(r"(?:\$" + ticker + r"\b|\(" + ticker + r"\)|(?:NASDAQ|NYSE|AMEX)\s*:\s*" + ticker + r"\b)", text, re.I):
        return symbol
    return None


def _rank_news(articles, symbol, company_name):
    names = _news_terms(symbol, company_name)
    direct, mentions, other = [], [], []
    for article in articles:
        title_hit = _news_match(article["title"], symbol, names)
        summary_hit = _news_match(article.get("summary"), symbol, names)
        item = dict(article)
        if title_hit:
            item["relevance"] = "Company mentioned in headline: " + title_hit
            direct.append(item)
        elif summary_hit and not any(
            other_symbol != symbol and not set(a.lower() for a in aliases).intersection(n.lower() for n in names)
            and _news_match(article["title"], other_symbol, aliases)
            for other_symbol, aliases in _NEWS_ALIASES.items()
        ):
            item["relevance"] = "Summary mentions " + summary_hit + "; company may not be the main subject"
            mentions.append(item)
        else:
            item["relevance"] = "Company relevance not confirmed"
            other.append(item)
    return direct, mentions, other


def _parse_news(raw):
    if not isinstance(raw, list):
        raise ValueError("Unexpected news response")
    articles, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        record = entry.get("content") or entry
        if not isinstance(record, dict):
            continue
        canonical = record.get("canonicalUrl") or {}
        link = canonical.get("url") if isinstance(canonical, dict) else None
        link = link or record.get("link")
        if not isinstance(link, str):
            continue
        parsed = urlsplit(link)
        if parsed.scheme != "https" or not parsed.netloc or link in seen:
            continue
        title = str(record.get("title") or "").strip()
        if not title:
            continue
        seen.add(link)
        provider = record.get("provider") or {}
        publisher = (provider.get("displayName") if isinstance(provider, dict) else None) or record.get("publisher") or "Publisher unavailable"
        raw_date = record.get("pubDate") or record.get("providerPublishTime")
        try:
            date = pd.to_datetime(raw_date, unit="s" if isinstance(raw_date, (int,float)) else None, utc=True)
            date_text = date.strftime("%b %d, %Y · %H:%M UTC") if pd.notna(date) else "Date unavailable"
            sort_date = date.value if pd.notna(date) else 0
        except (ValueError, TypeError, OverflowError):
