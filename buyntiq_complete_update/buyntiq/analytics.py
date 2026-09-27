"""One transparent research score for individual stocks and both portfolio tools."""
from concurrent.futures import ThreadPoolExecutor
import time
import numpy as np
import pandas as pd
from buyntiq import data
from buyntiq.legacy_calculations import technical_analysis_from_data, fundamental_score


def signal(score):
    if score >= 75:
        return "Strong setup"
    if score >= 60:
        return "Constructive"
    if score >= 45:
        return "Mixed"
    return "Weak setup"


def combined_score(technical, fundamental, coverage, forecast):
    parts = {"Technical": (float(technical), .55)}
    if fundamental is not None:
        parts["Company"] = (float(fundamental), .45 * min(1, max(0, coverage) / 6))
    total = sum(w for _, w in parts.values())
    ml_weight = 0.0
    if forecast and forecast.get("available"):
        ml_weight = .25 * forecast["evidence_weight"]
    components = {name: {"score": score, "weight": weight / total * (1-ml_weight)} for name, (score, weight) in parts.items()}
    if ml_weight:
        # A smooth bounded response avoids huge forecasts dominating the score.
        raw = 50 + 50 * np.tanh(forecast["predicted_return"] / .20)
        components["ML · " + {21:"1 month",63:"3 months",126:"6 months",252:"1 year"}.get(forecast.get("horizon",63), str(forecast.get("horizon")))] = {"score": float(raw), "weight": ml_weight}
    score = sum(v["score"]*v["weight"] for v in components.values())
    return float(score), components


def analyze(symbol, demo=False, history=None, include_ml=True, refresh=False, horizon=63):
    start = time.perf_counter()
    symbol = data.normalize_symbol(symbol)
    if not symbol:
        raise ValueError("Enter a valid ticker.")
    # Network reads overlap; model training is bounded independently.
    with ThreadPoolExecutor(max_workers=2) as pool:
        company_job = pool.submit(data.company, symbol, demo, refresh)
        frame = history if history is not None else data.prices(symbol, demo, refresh)
        company = company_job.result()
    technical = technical_analysis_from_data(frame)
    if technical is None:
        raise ValueError("At least 63 valid daily prices are needed for research.")
    fund_score, fund_notes = fundamental_score(company)
    forecast = None
    model_error = None
    if include_ml:
        try:
            from buyntiq.model import forecast as run_forecast
            forecast = run_forecast(frame, horizon)
        except Exception as exc:
            model_error = f"Forecast unavailable ({type(exc).__name__}). Technical and company research is still available."
    if include_ml and not (forecast or {}).get("available"):
        from buyntiq.model import short_history_forecast
        forecast = short_history_forecast(frame, horizon, (forecast or {}).get("reason") or model_error or "Full model unavailable")
    if forecast and forecast.get("available"):
        forecast.setdefault("forecast_kind", "Validated ensemble" if forecast.get("evidence_weight", 0) > 0 else "Ensemble (weak evidence)")
    score, components = combined_score(technical["technical_score"], fund_score, company.get("coverage", 0), forecast)
    return {"symbol": symbol, "company": company, "technical": technical, "fundamental_score": fund_score,
            "fundamental_notes": fund_notes, "forecast": forecast, "score": score, "components": components,
            "signal": signal(score), "prices": frame, "model_error": model_error,
            "as_of": str(frame.index[-1].date()), "demo": demo,
            "elapsed": round(time.perf_counter()-start, 2), "created_at": pd.Timestamp.now(tz="UTC").isoformat()}


def model_brief(result):
    """Evidence-driven explanation; no external LLM or invented company facts."""
    t, f = result["technical"], result.get("forecast")
    points = []
    ma200 = t.get("ma200")
    if ma200:
        points.append(f"Trend: the adjusted close is {(t['price']/ma200-1):+.1%} from its 200-session average.")
    points.append(f"Risk: historical annualized volatility is {t['annualized_volatility']:.1%}; this measures past price variation.")
    if result["fundamental_score"] is not None:
        points.append(f"Company: {result['company'].get('coverage', 0)} of 6 financial metrics contribute to a {result['fundamental_score']:.0f}/100 company score.")
    else:
        points.append("Company data is unavailable. The score uses the remaining components.")
    if f and f.get("available"):
        weight = result["components"].get("ML · 3 months", {}).get("weight", 0)
        if weight > 0:
            points.append(f"ML: held-out return error was {f['holdout_skill']:.1%} lower than the selected baseline. Its share of the research score is {weight:.1%}.")
        else:
            points.append("ML: the ensemble did not demonstrate an error advantage in both development and holdout periods. It has zero weight in the research score.")
    return points
