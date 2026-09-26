"""On-demand market data with bounded requests, caching, and an explicit demo."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from io import StringIO
import hashlib
import re
import time
import numpy as np
import pandas as pd
from buyntiq import cache

PRICE_TTL = 6 * 3600
COMPANY_TTL = 12 * 3600

# A deliberately disclosed starting universe, not a claim to cover the market.
STARTER = {
    "Technology": "AAPL MSFT NVDA AMD AVGO ORCL CRM ADBE MU INTC",
    "Communication Services": "GOOGL META NFLX DIS TMUS VZ",
    "Consumer Cyclical": "AMZN TSLA HD MCD NKE SBUX",
    "Consumer Defensive": "WMT COST PG KO PEP CL",
    "Financial Services": "JPM BAC GS V MA BRK-B",
    "Healthcare": "LLY JNJ UNH ABBV MRK AMGN",
    "Industrials": "CAT GE RTX UPS HON DE",
    "Energy": "XOM CVX COP SLB",
    "Basic Materials": "LIN APD FCX NEM",
    "Utilities": "NEE SO DUK AEP",
    "Real Estate": "PLD AMT EQIX O",
}
SECTORS = {symbol: sector for sector, symbols in STARTER.items() for symbol in symbols.split()}


def normalize_symbol(value):
    if value is None:
        return None
    value = str(value).strip().upper().replace(".", "-")
    return value if re.fullmatch(r"[A-Z][A-Z0-9-]{0,11}", value) else None


def parse_symbols(text, limit=100):
    tokens = re.split(r"[,;\s]+", str(text).strip())
    return list(dict.fromkeys(s for t in tokens if (s := normalize_symbol(t))))[:limit]


def starter_universe():
    return pd.DataFrame([{"Symbol": s, "Sector": sector, "Company": s} for s, sector in SECTORS.items()])


def universe(broad=False):
    if not broad:
        return starter_universe()
    saved = cache.read("universe", "us-v1", 24 * 3600)
    if saved:
        return pd.DataFrame(saved)
    from buyntiq.legacy_calculations import _fetch_us_stock_universe
    result = _fetch_us_stock_universe()
    if result.empty:
        raise ValueError("The US listing directory is unavailable. Use the starter list or your own symbols.")
    cache.write("universe", "us-v1", result.to_dict("records"))
    return result


def clean_prices(frame, symbol=None):
    if frame is None or frame.empty:
        return None
    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        for level in range(frame.columns.nlevels):
            if symbol in frame.columns.get_level_values(level):
                frame = frame.xs(symbol, axis=1, level=level)
                break
        if isinstance(frame.columns, pd.MultiIndex):
            return None
    if "Close" not in frame:
        return None
    frame = frame.loc[:, [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in frame]].apply(pd.to_numeric, errors="coerce")
    frame.index = pd.to_datetime(frame.index)
    if frame.index.tz is not None:
        frame.index = frame.index.tz_localize(None)
    frame.index = frame.index.normalize()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["Close"])
    frame = frame[frame.Close > 0]
    if "Volume" not in frame:
        frame["Volume"] = 0.0
    frame["Volume"] = frame.Volume.fillna(0).clip(lower=0)
    return frame.astype(float) if len(frame) >= 2 else None


def _read_prices(symbol, ttl=PRICE_TTL):
    saved = cache.read("prices", symbol, ttl)
    if saved is None:
        return None
    try:
        payload = saved["frame"]
        frame = pd.DataFrame(payload["data"], columns=payload["columns"], index=pd.to_datetime(payload["index"]), dtype=float)
        frame.attrs.update(saved["meta"])
        return frame
    except (ValueError, KeyError, TypeError):
        return None


def _save_prices(symbol, frame):
    frame.attrs.update(source="Yahoo Finance · adjusted daily history", fetched_at=pd.Timestamp.now(tz="UTC").isoformat(), stale=False, demo=False)
    serializable = frame.astype(object).where(frame.notna(), None)
    payload = {"columns": list(frame.columns), "index": [str(d) for d in frame.index], "data": serializable.to_numpy().tolist()}
    cache.write("prices", symbol, {"frame": payload, "meta": frame.attrs})
    return frame


def _stock(symbol):
    import yfinance as yf
    from curl_cffi import requests
    # yfinance's company endpoints otherwise inherit long network timeouts.
    return yf.Ticker(symbol, session=requests.Session(impersonate="chrome", timeout=8))


def prices(symbol, demo=False, refresh=False):
    symbol = normalize_symbol(symbol)
    if not symbol:
        raise ValueError("Enter a valid US-listed ticker, such as AAPL or BRK-B.")
    if demo:
        return demo_prices(symbol).copy()
    with cache.lock_for("prices-" + symbol):
        saved = _read_prices(symbol)
        if saved is not None and not refresh:
            return saved
        failed = cache.read("failures", "prices-" + symbol, 60)
        try:
            if failed and not refresh:
                raise ValueError("The data provider recently declined this request. Retry in a minute.")
            frame = clean_prices(_stock(symbol).history(period="10y", auto_adjust=True, timeout=8, raise_errors=True), symbol)
            if frame is not None:
                return _save_prices(symbol, frame)
        except Exception:
            pass
        cache.write("failures", "prices-" + symbol, {"failed": True})
        stale = _read_prices(symbol, 7 * 24 * 3600)
        if stale is not None:
            stale.attrs["stale"] = True
            return stale
        raise ValueError(f"No price history for {symbol}. Check the symbol or retry when the provider is available.")


def batch_prices(symbols, demo=False, progress=None):
    """Batch network work; never retries every name when the provider is down."""
    symbols = list(dict.fromkeys(symbols))
    frames, errors, missing = {}, {}, []
    for s in symbols:
        frame = demo_prices(s).copy() if demo else _read_prices(s)
        if frame is not None:
            frames[s] = frame
        else:
            missing.append(s)
    if missing:
        import yfinance as yf
        for offset in range(0, len(missing), 32):
            chunk = missing[offset:offset+32]
            try:
                batch = yf.download(chunk, period="10y", auto_adjust=True, progress=False,
                                    group_by="ticker", threads=4, timeout=8)
            except Exception:
                batch = None
            for s in chunk:
                frame = clean_prices(batch, s)
                if frame is not None:
                    frames[s] = _save_prices(s, frame)
                else:
                    stale = _read_prices(s, 7 * 24 * 3600)
                    if stale is not None:
                        stale.attrs["stale"] = True
                        frames[s] = stale
                    else:
                        errors[s] = "Price history unavailable"
                        cache.write("failures", "prices-" + s, {"failed": True})
            if progress:
                progress(len(frames) + len(errors), len(symbols))
            # Stop further calls after a whole failed chunk, e.g. rate limiting.
            if all(s not in frames for s in chunk):
                for s in missing[offset+32:]:
                    errors[s] = "Not requested after provider batch failure"
                break
    return frames, errors


def _finite(value):
    try:
        number = float(value)
        return number if np.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def company(symbol, demo=False, refresh=False):
    if demo:
        return demo_company(symbol)
    with cache.lock_for("company-" + symbol):
        saved = cache.read("company", symbol, COMPANY_TTL)
        if saved and not refresh:
            return saved
        if cache.read("failures", "company-" + symbol, 60) and not refresh:
            info = {}
        else:
            try:
                info = _stock(symbol).get_info() or {}
            except Exception:
                info = {}
        if not info or not any(k in info for k in ("shortName", "longName", "quoteType")):
            cache.write("failures", "company-" + symbol, {"failed": True})
            stale = cache.read("company", symbol, 7 * 24 * 3600)
            return dict(stale, stale=True) if stale else {"company_name": symbol, "status": "unavailable", "sector": SECTORS.get(symbol, "Unknown"), "currency": None, "coverage": 0}
        result = {
            "company_name": info.get("longName") or info.get("shortName") or symbol,
            "sector": info.get("sector") or SECTORS.get(symbol, "Unknown"),
            "currency": info.get("currency"), "financial_currency": info.get("financialCurrency"),
            "quote_type": info.get("quoteType"), "description": info.get("longBusinessSummary", ""),
            "market_cap": _finite(info.get("marketCap")),
            "revenue_growth": _finite(info.get("revenueGrowth")),
            "earnings_growth": _finite(info.get("earningsGrowth")),
            "profit_margin": _finite(info.get("profitMargins")),
            "forward_pe": _finite(info.get("forwardPE")),
            "debt_to_equity": (_finite(info.get("debtToEquity")) / 100 if _finite(info.get("debtToEquity")) is not None else None),
            "free_cash_flow": _finite(info.get("freeCashflow")),
            "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(), "stale": False,
            "status": "available", "source": "Yahoo Finance company snapshot",
        }
        # Negative book equity makes a debt/equity ratio misleading as a score.
        if result["debt_to_equity"] is not None and result["debt_to_equity"] < 0:
            result["debt_to_equity"] = None
        result["coverage"] = sum(result.get(k) is not None for k in ("revenue_growth", "earnings_growth", "profit_margin", "forward_pe", "debt_to_equity", "free_cash_flow"))
        cache.write("company", symbol, result)
        return result


def news(symbol, demo=False):
    if demo:
        return []
    cached = cache.read("news", symbol, 3600)
    if cached is not None:
        return cached
    try:
        raw = _stock(symbol).get_news(count=8) or []
        items = []
        for record in raw:
            record = record.get("content") or record
            link = (record.get("canonicalUrl") or {}).get("url") or record.get("link", "")
            if not str(link).startswith("https://"):
                continue
            items.append({"title": record.get("title", "News article"), "url": link,
                          "publisher": (record.get("provider") or {}).get("displayName", ""), "date": str(record.get("pubDate", ""))[:10]})
        cache.write("news", symbol, items)
        return items
    except Exception:
        return []


@lru_cache(maxsize=100)
def demo_prices(symbol):
    """Deterministic synthetic data, never used as a live fallback."""
    seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(end="2026-09-25", periods=2520)
    market = np.random.default_rng(714).normal(.00018, .009, len(index))
    noise = rng.normal(0, .007 + (seed % 10) / 1800, len(index))
    returns = market * (.6 + (seed % 7) / 10) + noise + .00008
    close = (30 + seed % 180) * np.exp(np.cumsum(returns))
    frame = pd.DataFrame({"Open": close * (1 + rng.normal(0, .003, len(index))),
                          "High": close * 1.008, "Low": close * .992, "Close": close,
                          "Volume": rng.integers(1_000_000, 8_000_000, len(index))}, index=index)
    frame.attrs.update(source="Synthetic demo data", fetched_at="2026-09-26T00:00:00Z", stale=False, demo=True)
    return frame


def demo_company(symbol):
    seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    return {"company_name": f"{symbol} · synthetic example", "sector": SECTORS.get(symbol, "Technology"),
            "currency": "USD", "financial_currency": "USD", "quote_type": "EQUITY", "description": "Demonstration company metrics. These are generated values, not company financial statements.",
            "market_cap": float(rng.uniform(1e10, 1e12)), "revenue_growth": float(rng.uniform(-.05, .3)),
            "earnings_growth": float(rng.uniform(-.1, .4)), "profit_margin": float(rng.uniform(.02, .3)),
            "forward_pe": float(rng.uniform(12, 50)), "debt_to_equity": float(rng.uniform(.1, 1.5)),
            "free_cash_flow": float(rng.uniform(1e8, 1e10)), "coverage": 6,
            "status": "available", "source": "Synthetic demo data", "stale": False, "fetched_at": "2026-09-26T00:00:00Z"}
