"""Shared, hourly-cached market/sector histories for forecasts.

No API key or new dependency. Missing benchmarks never prevent stock research.
The sector mapping describes today's classification, not historical membership.
"""
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from buyntiq import data

SECTOR_ETFS = {
    "Technology": "XLK", "Communication Services": "XLC",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
    "Financial Services": "XLF", "Healthcare": "XLV", "Industrials": "XLI",
    "Energy": "XLE", "Basic Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE",
}


def load_context(sector, demo=False, refresh=False):
    """Return available histories and explicit provider/coverage notices."""
    symbols = {"market": "SPY"}
    sector_symbol = SECTOR_ETFS.get(data.normalize_sector(sector))
    notes = []
    if sector_symbol:
        symbols["sector"] = sector_symbol
    else:
        notes.append("Sector benchmark unavailable: no verified sector mapping.")
    frames = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {role: pool.submit(data.prices, symbol, demo, refresh)
                for role, symbol in symbols.items()}
        for role, job in jobs.items():
            symbol = symbols[role]
            try:
                frame = job.result().copy()
                frame.attrs["benchmark_symbol"] = symbol
                frames[role] = frame
            except Exception:
                notes.append(f"{symbol} {role} history unavailable from the provider.")
    return frames, notes


def prepare_context(prices, context=None):
    """Ignore future rows and reject stale, incomplete, or synthetic/live mixes."""
    usable, report = {}, []
    cutoff = pd.Timestamp(prices.index[-1]).normalize()
    stock_is_demo = bool(prices.attrs.get("demo", False))
    for role in ("market", "sector"):
        raw = (context or {}).get(role)
        if raw is None:
            continue
        symbol = raw.attrs.get("benchmark_symbol", role)
        row = {"role": role, "symbol": symbol, "status": "Unavailable"}
        frame = data.clean_prices(raw)
        if frame is not None:
            frame = frame.loc[frame.index <= cutoff]
        if raw.attrs.get("stale", False):
            row["reason"] = "Provider supplied saved data after a failure."
        elif bool(raw.attrs.get("demo", False)) != stock_is_demo:
            row["reason"] = "Benchmark and stock must use the same live/demo mode."
        elif frame is None or len(frame) < 200:
            row["reason"] = "Fewer than 200 usable benchmark sessions."
        elif cutoff not in frame.index:
            row["reason"] = "Benchmark is missing the stock's latest session."
        else:
            frame.attrs.update(raw.attrs)
            usable[role] = frame
            row.update(status="Available", as_of=str(frame.index[-1].date()),
                       sessions=len(frame), reason="Exact-date alignment; no future filling.")
        report.append(row)
    return usable, report
