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
            repeated_sector = sum(c["company"].get("sector") == r["company"].get("sector") for c in chosen) if r["company"].get("sector") not in (None, "Unknown") else 0
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


def build(symbols, count=5, budget=10000, profile="Balanced", method="Highest scores", finalists=12, demo=False, progress=None, sector_hints=None, positive_only=True, horizon=63):
    if progress:
        progress(.02, "Loading price histories")
    if horizon not in (21,63,126,252):
        raise ValueError("Choose a 1m, 3m, 6m, or 1y horizon.")
    symbols = list(dict.fromkeys(symbols))
    errors, frames, screened = {}, {}, []
    keep = max(count, finalists)
    # Screen bounded chunks; retain only finalist histories to limit memory.
    for offset in range(0, len(symbols), 64):
        chunk = symbols[offset:offset+64]
        current, failed = data.batch_prices(chunk, demo)
        errors.update(failed)
        if not current:
            for symbol in symbols[offset+64:]:
                errors[symbol] = "Not requested after provider batch failure; scan incomplete"
            break
        for symbol, frame in current.items():
            technical = technical_analysis_from_data(frame)
            if technical:
                screened.append((symbol, technical["technical_score"]))
                frames[symbol] = frame
            else:
                errors[symbol] = "Fewer than 63 valid daily prices"
        screened.sort(key=lambda row: (-row[1], row[0]))
        retained = {symbol for symbol, _ in screened[:keep]}
        frames = {symbol: frame for symbol, frame in frames.items() if symbol in retained}
        if progress:
            progress(.05+.25*min(offset+len(chunk),len(symbols))/max(len(symbols),1), f"Price screen · {min(offset+len(chunk),len(symbols))}/{len(symbols)} listings")
    shortlist = [s for s, _ in screened[:keep]]
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(analyze, s, demo, frames[s], horizon=horizon): s for s in shortlist}
        for index, job in enumerate(as_completed(jobs), 1):
            symbol = jobs[job]
            try:
                result = job.result()
                sector, source = data.resolve_sector(symbol, result["company"].get("sector"), (sector_hints or {}).get(symbol))
                result["company"].update(sector=sector, sector_source=source)
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
    for r in ranked:
        reason = recommendation_exclusion(r, positive_only)
        if reason:
            errors[r["symbol"]] = reason
    eligible = [r for r in ranked if not recommendation_exclusion(r, positive_only)]
    if not eligible:
        raise ValueError("No analyzed candidates meet the known-sector, complete-data, and positive-return requirements. No portfolio was built. Increase Full company + ML analyses or try a broader universe.")
    chosen = choose_holdings(eligible, count, method)
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
                     "ML forecast": model.get("predicted_return"), "Forecast method": model.get("forecast_kind", "Unavailable"), "Forecast note": model.get("reason", "Ensemble estimate"), "Sector source": r["company"].get("sector_source", "Company provider"), "ML score weight": sum(v["weight"] for k,v in r["components"].items() if k.startswith("ML ·")), "Price date": r["as_of"]})
    table = pd.DataFrame(rows)
    chosen_symbols = {r["symbol"] for r in chosen}
    ranking = pd.DataFrame([{"Rank": i, "Ticker": r["symbol"], "Final score": r["score"],
                             "Technical": r["technical"]["technical_score"], "Company": r["fundamental_score"],
                             "Forecast return (%)": 100*(r.get("forecast") or {}).get("predicted_return", float("nan")),
                             "Eligibility": recommendation_exclusion(r, positive_only) or "Eligible",
                             "ML score weight": sum(v["weight"] for k,v in r["components"].items() if k.startswith("ML ·")),
                             "Selected": r["symbol"] in chosen_symbols} for i, r in enumerate(ranked, 1)])
    spent = float((table["Whole shares"] * table.Price).sum())
    risk = portfolio_risk(chosen, weights)
    if progress:
        progress(1., "Portfolio ready")
    return {"table": table, "ranking": ranking, "results": chosen, "score": float(np.dot(weights, [r["score"] for r in chosen])),
            "risk": risk, "errors": errors, "budget": budget, "cash": max(0, budget-spent), "profile": profile,
            "horizon": horizon, "universe_count": len(symbols), "method": method, "positive_only": positive_only, "positive_candidates": len(eligible), "requested": count, "screened": len(screened), "analyzed": len(results),
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
                     "Sector": r["company"].get("sector", "Unknown"), "ML forecast": f.get("predicted_return"), "Forecast method": f.get("forecast_kind", "Unavailable"), "Forecast note": f.get("reason", "Ensemble estimate"),
                     "ML score weight": sum(v["weight"] for k,v in r["components"].items() if k.startswith("ML ·")), "Price date": r["as_of"]})
    return {"table": pd.DataFrame(rows), "results": results, "errors": errors, "total": float(values.sum()),
            "score": float(np.dot(weights, [r["score"] for r in results])), "risk": portfolio_risk(results, weights),
            "effective_holdings": float(1/np.sum(weights**2)), "demo": demo,
            "created_at": pd.Timestamp.now(tz="UTC").isoformat()}


def positive_forecast(result):
    value = (result.get("forecast") or {}).get("predicted_return")
    return value is not None and np.isfinite(value) and value > 0


def projected_portfolio(table, value, whole_shares=False):
    """Sum dollar gains; never treat missing forecasts as zero return."""
    forecasts = pd.to_numeric(table["ML forecast"], errors="coerce")
    amounts = table["Whole shares"] * table.Price if whole_shares else table.Weight * value
    active = amounts > 0
    valid = np.isfinite(forecasts)
    coverage = float(amounts[active & valid].sum()/amounts[active].sum()) if active.any() else 1.
    if (active & ~valid).any():
        return {"available": False, "coverage": coverage}
    gain = float((amounts[active] * forecasts[active]).sum())
    return {"available": True, "coverage": coverage, "gain": gain,
            "return": gain/value if value > 0 else 0., "end_value": value+gain,
            "cash": max(0., float(value-amounts.sum()))}


def recommendation_exclusion(result, positive_only=True):
    company = result.get("company") or {}
    if data.normalize_sector(company.get("sector")) == "Unknown":
        return "Excluded: unknown sector"
    if company.get("status") == "unavailable" or not company.get("company_name"):
        return "Excluded: unavailable company profile"
    if result.get("fundamental_score") is None:
        return "Excluded: missing company financial metrics"
    price = result.get("technical", {}).get("price")
    if price is None or not np.isfinite(price) or price <= 0:
        return "Excluded: invalid price"
    forecast = (result.get("forecast") or {}).get("predicted_return")
    if forecast is None or not np.isfinite(forecast):
        return "Excluded: missing forecast"
    if positive_only and forecast <= 0:
        return "Excluded: nonpositive forecast"
    return None
