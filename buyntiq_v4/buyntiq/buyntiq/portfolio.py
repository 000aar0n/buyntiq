"""Final-score selection, explicit diversification, and honest coverage reporting."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
from buyntiq import data
from buyntiq.analytics import analyze
from buyntiq.legacy_calculations import technical_analysis_from_data

PROFILES = {"Conservative": {"power": 1.3, "cap": .25}, "Balanced": {"power": .8, "cap": .35}, "Aggressive": {"power": .35, "cap": .45}}


def normalize_entries(entries):
    totals = {}
    for _, row in entries.iterrows():
        raw = str(row.get("Ticker", "") or "").strip()
        amount = row.get("Shares", 0)
        if not raw or raw.lower() == "nan":
            if pd.notna(amount) and float(amount or 0) != 0:
                raise ValueError("A row with shares must also have a ticker.")
            continue
        symbol = data.normalize_symbol(raw)
        if not symbol:
            raise ValueError(f"Invalid ticker: {raw}")
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise ValueError(f"Enter a numeric share count for {symbol}.") from None
        if not np.isfinite(amount) or amount <= 0:
            raise ValueError(f"Shares for {symbol} must be greater than zero.")
        if amount > 1e12:
            raise ValueError(f"Share count for {symbol} is too large. Check the units.")
        totals[symbol] = totals.get(symbol, 0) + amount
    if not totals:
        raise ValueError("Add at least one ticker and a positive share count.")
    if len(totals) > 50:
        raise ValueError("Review up to 50 different holdings at a time.")
    return totals


def capped_weights(raw, cap):
    raw = np.maximum(np.asarray(raw, dtype=float), 1e-12)
    if len(raw) == 0:
        raise ValueError("Cannot allocate an empty portfolio.")
    cap = max(float(cap), 1/len(raw))
    result = np.zeros(len(raw))
    free = np.ones(len(raw), dtype=bool)
    remaining = 1.0
    for _ in range(len(raw)+1):
        proposed = raw[free] / raw[free].sum() * remaining
        high = proposed > cap + 1e-12
        if not high.any():
            result[free] = proposed
            break
        indices = np.where(free)[0][high]
        result[indices] = cap
        free[indices] = False
        remaining = 1 - result.sum()
    return result, cap


def choose_holdings(results, count, method):
    ranked = sorted(results, key=lambda r: (-r["score"], r["symbol"]))
    if method == "Highest scores":
        return ranked[:count]
    chosen = []
    candidates = ranked.copy()
    while candidates and len(chosen) < count:
        def adjusted(r):
            correlations = []
            ret = r["prices"].Close.pct_change().tail(252)
            for other in chosen:
                corr = ret.corr(other["prices"].Close.pct_change().tail(252))
                if pd.notna(corr):
                    correlations.append(max(0, corr))
            repeated_sector = sum(c["company"].get("sector") == r["company"].get("sector") for c in chosen)
            return r["score"] - 12*max(correlations, default=0) - 4*repeated_sector
        best = max(candidates, key=adjusted)
        chosen.append(best)
        candidates = [r for r in candidates if r["symbol"] != best["symbol"]]
    return chosen


def portfolio_risk(results, weights):
    from sklearn.covariance import LedoitWolf
    if not results:
        return None
    returns = pd.concat({r["symbol"]: r["prices"].Close.pct_change().tail(252) for r in results}, axis=1).dropna()
    if len(returns) < 63:
        return None
    covariance = LedoitWolf().fit(returns.to_numpy()).covariance_ * 252
    vol = float(np.sqrt(np.asarray(weights) @ covariance @ np.asarray(weights)))
    correlations = returns.corr()
    return {"volatility": vol, "observations": len(returns), "correlations": correlations}


def build(symbols, count=5, budget=10000, profile="Balanced", method="Highest scores", finalists=12, demo=False, progress=None):
    if progress:
        progress(.02, "Loading price histories")
    frames, errors = data.batch_prices(symbols, demo, lambda done, total: progress(.05+.25*done/max(total,1), f"Price histories · {done}/{total}") if progress else None)
    screened = []
    for s, frame in frames.items():
        technical = technical_analysis_from_data(frame)
        if technical:
            screened.append((s, technical["technical_score"]))
        else:
            errors[s] = "Fewer than 63 valid daily prices"
    screened.sort(key=lambda row: (-row[1], row[0]))
    shortlist = [s for s, _ in screened[:max(count, finalists)]]
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(analyze, s, demo, frames[s]): s for s in shortlist}
        for index, job in enumerate(as_completed(jobs), 1):
            symbol = jobs[job]
            try:
                result = job.result()
                if result["company"].get("currency") != "USD":
                    errors[symbol] = "USD quote currency could not be verified"
                else:
                    results.append(result)
            except Exception as exc:
                errors[symbol] = str(exc)
            if progress:
                progress(.30 + .65*index/max(len(shortlist),1), f"Company + ML analysis · {index}/{len(shortlist)}")
    if not results:
        raise ValueError("No eligible USD-priced stocks could be analyzed. Check the provider or try demo mode.")
    ranked = sorted(results, key=lambda r: (-r["score"], r["symbol"]))
    chosen = choose_holdings(ranked, count, method)
    settings = PROFILES[profile]
    raw = [(max(r["score"], 10)/100) / max(r["technical"]["annualized_volatility"], .10)**settings["power"] for r in chosen]
    weights, effective_cap = capped_weights(raw, settings["cap"])
    rows = []
    for r, w in zip(chosen, weights):
        allocation, price = budget*w, r["technical"]["price"]
        model = r.get("forecast") or {}
        rows.append({"Ticker": r["symbol"], "Company": r["company"]["company_name"], "Sector": r["company"].get("sector", "Unknown"),
                     "Weight": float(w), "Score": r["score"], "Signal": r["signal"], "Price": price,
                     "Target allocation": float(allocation), "Whole shares": int(allocation//price),
                     "ML forecast": model.get("predicted_return"), "ML score weight": r["components"].get("ML · 3 months", {}).get("weight", 0), "Price date": r["as_of"]})
    table = pd.DataFrame(rows)
    chosen_symbols = {r["symbol"] for r in chosen}
    ranking = pd.DataFrame([{"Rank": i, "Ticker": r["symbol"], "Final score": r["score"],
                             "Technical": r["technical"]["technical_score"], "Company": r["fundamental_score"],
                             "ML score weight": r["components"].get("ML · 3 months", {}).get("weight", 0),
                             "Selected": r["symbol"] in chosen_symbols} for i, r in enumerate(ranked, 1)])
    spent = float((table["Whole shares"] * table.Price).sum())
    risk = portfolio_risk(chosen, weights)
    if progress:
        progress(1., "Portfolio ready")
    return {"table": table, "ranking": ranking, "results": chosen, "score": float(np.dot(weights, [r["score"] for r in chosen])),
            "risk": risk, "errors": errors, "budget": budget, "cash": max(0, budget-spent), "profile": profile,
            "method": method, "requested": count, "screened": len(screened), "analyzed": len(results),
            "effective_cap": effective_cap, "nominal_cap": settings["cap"], "demo": demo,
            "created_at": pd.Timestamp.now(tz="UTC").isoformat()}


def review(holdings, demo=False, progress=None):
    results, errors = [], {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(analyze, s, demo): s for s in holdings}
        for i, job in enumerate(as_completed(jobs), 1):
            symbol = jobs[job]
            try:
                r = job.result()
                if r["company"].get("currency") != "USD":
                    errors[symbol] = "USD quote currency could not be verified"
                else:
                    results.append(r)
            except Exception as exc:
                errors[symbol] = str(exc)
            if progress:
                progress(i/len(holdings), f"Reviewing holdings · {i}/{len(holdings)}")
    if not results:
        raise ValueError("None of these holdings could be priced and verified in USD.")
    results.sort(key=lambda r: r["technical"]["price"]*holdings[r["symbol"]], reverse=True)
    values = np.array([r["technical"]["price"]*holdings[r["symbol"]] for r in results])
    weights = values/values.sum()
    rows = []
    for r, value, weight in zip(results, values, weights):
        f = r.get("forecast") or {}
        rows.append({"Ticker": r["symbol"], "Shares": holdings[r["symbol"]], "Price": r["technical"]["price"],
                     "Value": float(value), "Weight": float(weight), "Score": r["score"], "Signal": r["signal"],
                     "Sector": r["company"].get("sector", "Unknown"), "ML forecast": f.get("predicted_return"),
                     "ML score weight": r["components"].get("ML · 3 months", {}).get("weight", 0), "Price date": r["as_of"]})
    return {"table": pd.DataFrame(rows), "results": results, "errors": errors, "total": float(values.sum()),
            "score": float(np.dot(weights, [r["score"] for r in results])), "risk": portfolio_risk(results, weights),
            "effective_holdings": float(1/np.sum(weights**2)), "demo": demo,
            "created_at": pd.Timestamp.now(tz="UTC").isoformat()}
