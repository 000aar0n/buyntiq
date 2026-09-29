"""Market-aware forecasting with development-only model selection.

Every target must mature before the following validation block starts. Candidate
selection, interval calibration, and the reported final test use separate dates.
More inputs are useful only when their chronological validation supports them.
"""
import hashlib
import time
import threading
import numpy as np
import pandas as pd
from buyntiq import cache, data
from buyntiq.benchmarks import prepare_context
from buyntiq.features import dataset, feature_frame

VERSION = "buyntiq-ensemble-6.0.1"
_training_lock = threading.RLock()
HORIZONS = {"1 month": 21, "3 months": 63, "6 months": 126, "1 year": 252}


def temporal_partitions(n, horizon):
    holdout_size = max(252, 2 * horizon)
    calibration_size = max(126, horizon)
    test_start = n - holdout_size
    cal_end = test_start - horizon
    cal_start = cal_end - calibration_size
    dev_end = cal_start - horizon
    if dev_end < max(500, 200 + 3 * 84 + horizon):
        raise ValueError("More price history is needed for separate training, calibration, and holdout periods.")
    return dict(dev=np.arange(dev_end), calibration=np.arange(cal_start, cal_end),
                holdout=np.arange(test_start, n), holdout_train=np.arange(test_start - horizon))


def _models(contextual=False):
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    models = {
        "Ridge": make_pipeline(StandardScaler(), Ridge(alpha=40)),
        "Extra Trees": ExtraTreesRegressor(n_estimators=56, max_depth=6, min_samples_leaf=18,
                                           max_features=.8, n_jobs=1, random_state=42),
        "Gradient Boosting": HistGradientBoostingRegressor(max_iter=70, max_leaf_nodes=9,
                            l2_regularization=12, learning_rate=.045, min_samples_leaf=25,
                            early_stopping=False, random_state=42),
    }
    if contextual:
        # The imputer is fitted anew on each training block, never the test data.
        models = {name: make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), model)
                  for name, model in models.items()}
    return models


def _metric(actual, predicted):
    return float(np.mean(np.abs(np.expm1(actual) - np.expm1(predicted))))


def error_skill(mae, baseline_mae):
    return 0.0 if baseline_mae <= 1e-9 else 1 - mae / baseline_mae


def _digest(prices, horizon, context, demo=False):
    digest = hashlib.sha256(f"{VERSION}-{horizon}-demo={bool(demo)}".encode())
    digest.update(pd.util.hash_pandas_object(prices[["Close", "Volume"]], index=True).values.tobytes())
    for role, frame in sorted(context.items()):
        digest.update(role.encode())
        digest.update(pd.util.hash_pandas_object(frame[["Close"]], index=True).values.tobytes())
    return digest.hexdigest()


def _source_metadata(prices, demo):
    return {"training_data_source": "Synthetic demo" if demo else "Yahoo Finance",
            "synthetic_data": bool(demo), "training_price_rows": len(prices),
            "training_price_start": str(prices.index[0].date()),
            "training_price_end": str(prices.index[-1].date())}


def forecast(prices, horizon=63, context=None, *, demo=False):
    """Network-free model; cache changes with stock AND benchmark observations."""
    if horizon not in HORIZONS.values():
        raise ValueError("Supported horizons are 21, 63, 126, and 252 sessions.")
    data.require_price_source(prices, demo)
    for frame in (context or {}).values():
        if frame is not None:
            data.require_price_source(frame, demo)
    usable, report = prepare_context(prices, context)
    key = _digest(prices, horizon, usable, demo)
    with cache.lock_for(key):
        saved = cache.read("models", key, 6 * 3600)
        if saved:
            result = dict(saved, cache_hit=True)
        else:
            with _training_lock:
                result = _train(prices, horizon, usable)
            cache.write("models", key, result)
            result = dict(result, cache_hit=False)
    # Provider status is current metadata, not a cached claim of data freshness.
    result["context_report"] = report
    result.update(_source_metadata(prices, demo))
    result["data_stale"] = bool(prices.attrs.get("stale", False))
    if result["data_stale"]:
        result["evidence_weight"] = 0.0
    return result


def _choose_blend(actual, model_pred, baseline_pred, fold_sizes):
    """Baseline wins near ties and improvements confined to one period."""
    actual, model_pred, baseline_pred = map(np.asarray, (actual, model_pred, baseline_pred))
    base_loss = _metric(actual, baseline_pred)
    if base_loss <= 1e-9:
        return 0.0
    candidates = []
    cuts = np.cumsum([0] + list(fold_sizes))
    for alpha in (0., .25, .5, .75, 1.):
        pred = alpha * model_pred + (1 - alpha) * baseline_pred
        loss = _metric(actual, pred)
        wins = sum(_metric(actual[a:b], pred[a:b]) < _metric(actual[a:b], baseline_pred[a:b])
                   for a, b in zip(cuts[:-1], cuts[1:]))
        if alpha == 0 or (loss < .97 * base_loss and wins >= 2):
            candidates.append((alpha, loss))
    best = min(loss for _, loss in candidates)
    return min(alpha for alpha, loss in candidates if loss <= best * 1.02 + 1e-12)


def _choose_candidate(actual, candidates, fold_sizes):
    """Prefer stock-only unless a challenger improves by 3% in aggregate
    and wins on at least two development folds. No calibration/holdout input.
    """
    selected = "Stock-only"
    cuts = np.cumsum([0] + list(fold_sizes))
    for name, candidate in candidates.items():
        if name == "Stock-only":
            continue
        incumbent = candidates[selected]["prediction"]
        prediction = candidate["prediction"]
        wins = sum(_metric(actual[a:b], prediction[a:b]) < _metric(actual[a:b], incumbent[a:b])
                   for a, b in zip(cuts[:-1], cuts[1:]))
        if _metric(actual, prediction) < .97 * _metric(actual, incumbent) and wins >= 2:
            selected = name
    return selected


def _candidate_specs(prices, x, y, horizon, context, splits):
    current = feature_frame(prices).iloc[[-1]]
    specs = {"Stock-only": {"x": x, "current": current, "target": y, "contextual": False}}
    if not context:
        return specs
    enriched = feature_frame(prices, context)
    # Use only contexts with complete features today. Historical gaps remain
    # missing, so a newer ETF cannot erase years of the stock-only training set.
    roles = [role for role in context if enriched[f"{role}_available"].iloc[-1] == 1]
    if not roles:
        return specs
    used = {role: context[role] for role in roles}
    enriched = feature_frame(prices, used)
    spec = {"x": enriched.reindex(x.index), "current": enriched.iloc[[-1]],
            "target": y, "contextual": True, "roles": roles}
    specs["Market context"] = spec
    if "market" in used:
        market_close = used["market"].Close.reindex(prices.index)
        market_target = np.log(market_close.shift(-horizon) / market_close).reindex(x.index)
        beta = enriched.market_beta_126.reindex(x.index)
        excess = y - beta * market_target
        # Relative targets require actual, matured benchmark observations.
        if all(excess.iloc[train].notna().sum() >= 200 for train, _ in splits):
            specs["Market-relative"] = dict(spec, target=excess, market_target=market_target)
    return specs


def _member_predictions(spec, scale, train, test_x, test_scale):
    """Train on matured labels; reconstruct relative predictions without
    accessing the benchmark's future return at any prediction date.
    """
    train = np.asarray(train)
    train = train[np.isfinite(spec["target"].iloc[train].to_numpy())]
    if len(train) < 200:
        raise ValueError("Too few matured labels for this model candidate.")
    target = spec["target"].iloc[train].to_numpy() / scale.iloc[train].to_numpy()
    lo, hi = np.quantile(target, [.01, .99])
    offset = np.zeros(len(test_x))
    if "market_target" in spec:
        # A deliberately simple market prior, estimated from TRAINING labels.
        # Its shrinkage is fixed; the candidate is tested on total stock return.
        market_prior = .25 * float(spec["market_target"].iloc[train].dropna().tail(252).median())
        offset = test_x.market_beta_126.fillna(1.).to_numpy() * market_prior
    predictions, fitted = {}, {}
    for name, model in _models(spec["contextual"]).items():
        model.fit(spec["x"].iloc[train], np.clip(target, lo, hi))
        predictions[name] = np.clip(model.predict(test_x), -8, 8) * np.asarray(test_scale) + offset
        fitted[name] = model
    return predictions, fitted


def _baseline(y, train, name):
    return 0. if name == "No price change" else float(y.iloc[train].tail(252).median())


def _bound(x, positions, train, test, horizon):
    return {"train_last": str(x.index[train[-1]].date()),
            "test_first": str(x.index[test[0]].date()), "test_last": str(x.index[test[-1]].date()),
            "gap_sessions": int(positions.iloc[test[0]] - positions.iloc[train[-1]] - 1),
            "horizon": horizon}


def _walk_predictions(spec, y, scale, positions, tests, horizon, weights, baseline_name, alpha):
    predictions, baselines, bounds = [], [], []
    x = spec["x"]
    for offset in range(0, len(tests), max(63, horizon)):
        block = tests[offset:offset + max(63, horizon)]
        train = np.flatnonzero(positions.to_numpy() + horizon < positions.iloc[block[0]])
        assert len(train) and positions.iloc[train[-1]] + horizon < positions.iloc[block[0]]
        base = _baseline(y, train, baseline_name)
        if alpha:
            members, _ = _member_predictions(spec, scale, train, x.iloc[block], scale.iloc[block])
            raw = sum(weights[name] * members[name] for name in weights)
            pred = alpha * raw + (1 - alpha) * base
        else:
            pred = np.repeat(base, len(block))
        predictions.extend(pred)
        baselines.extend(np.repeat(base, len(block)))
        bounds.append(_bound(x, positions, train, block, horizon))
    return np.asarray(predictions), np.asarray(baselines), bounds


def _importance(fitted, spec):
    model = fitted["Extra Trees"]
    if spec["contextual"]:
        model = model.steps[-1][1]
    return dict(sorted(zip(spec["x"].columns, map(float, model.feature_importances_)),
                       key=lambda item: item[1], reverse=True)[:8])


def _train(prices, horizon, context=None):
    from sklearn.model_selection import TimeSeriesSplit
    from threadpoolctl import threadpool_limits
    start = time.perf_counter()
    x, y, scale, positions = dataset(prices, horizon)
    try:
        parts = temporal_partitions(len(x), horizon)
    except ValueError as exc:
        return {"available": False, "reason": str(exc), "model_version": VERSION}
    if x.empty or not np.isfinite(x.to_numpy()).all():
        return {"available": False, "reason": "Invalid historical features.", "model_version": VERSION}
    current = feature_frame(prices).iloc[[-1]]
    if not np.isfinite(current.to_numpy()).all():
        return {"available": False, "reason": "Latest session has incomplete features.", "model_version": VERSION}
    test_size = min(max(84, horizon), (len(parts["dev"]) - horizon - 200) // 3)
    splits = list(TimeSeriesSplit(n_splits=3, test_size=test_size, gap=horizon).split(x.iloc[parts["dev"]]))
    specs = _candidate_specs(prices, x, y, horizon, context or {}, splits)
    names = list(_models())
    actual = np.concatenate([y.iloc[test].to_numpy() for _, test in splits])
    fold_sizes = [len(test) for _, test in splits]
    fold_bounds = []
    baselines = {name: np.concatenate([np.repeat(_baseline(y, train, name), len(test))
                                      for train, test in splits])
                 for name in ("No price change", "Historical median")}
    baseline_name = min(baselines, key=lambda name: _metric(actual, baselines[name]))
    baseline_dev = baselines[baseline_name]
    candidates = {}
    with threadpool_limits(limits=1):
        for name, spec in specs.items():
            oof = {member: [] for member in names}
            for train, test in splits:
                assert positions.iloc[train[-1]] + horizon < positions.iloc[test[0]]
                predictions, _ = _member_predictions(spec, scale, train, spec["x"].iloc[test], scale.iloc[test])
                for member in names:
                    oof[member].extend(predictions[member])
                if name == "Stock-only":
                    fold_bounds.append(_bound(x, positions, train, test, horizon))
            losses = {member: _metric(actual, np.asarray(pred)) for member, pred in oof.items()}
            inverse = {member: 1 / max(loss, 1e-4) for member, loss in losses.items()}
            weights = {member: .5 / len(names) + .5 * inverse[member] / sum(inverse.values()) for member in names}
            raw = sum(weights[member] * np.asarray(oof[member]) for member in names)
            alpha = _choose_blend(actual, raw, baseline_dev, fold_sizes)
            prediction = alpha * raw + (1 - alpha) * baseline_dev
            candidates[name] = dict(weights=weights, alpha=alpha, prediction=prediction, errors=losses,
                                    mae=_metric(actual, prediction), raw_mae=_metric(actual, raw))
        selected = _choose_candidate(actual, candidates, fold_sizes)
        choice, spec = candidates[selected], specs[selected]
        weights, alpha = choice["weights"], choice["alpha"]
        dev_gain = error_skill(choice["mae"], _metric(actual, baseline_dev))
        cal, holdout = parts["calibration"], parts["holdout"]
        cal_pred, _, cal_bounds = _walk_predictions(spec, y, scale, positions, cal, horizon, weights, baseline_name, alpha)
        holdout_pred, base_test, test_bounds = _walk_predictions(spec, y, scale, positions, holdout, horizon, weights, baseline_name, alpha)
        # Signed residuals preserve asymmetric error tails. Overlapping labels
        # are dependent: this empirical range has no guaranteed 80% coverage.
        residuals = (y.iloc[cal].to_numpy() - cal_pred) / scale.iloc[cal].to_numpy()
        low_q = min(0., float(np.quantile(residuals, .1, method="lower")))
        high_q = max(0., float(np.quantile(residuals, .9, method="higher")))
        holdout_y = y.iloc[holdout].to_numpy()
        mae, baseline_mae = _metric(holdout_y, holdout_pred), _metric(holdout_y, base_test)
        skill = error_skill(mae, baseline_mae)
        accuracy = float(np.mean(np.sign(holdout_pred) == np.sign(holdout_y)))
        always_up = float(np.mean(holdout_y > 0))
        nonoverlap = np.arange(0, len(holdout), horizon)
        nonoverlap_accuracy = float(np.mean(np.sign(holdout_pred[nonoverlap]) == np.sign(holdout_y[nonoverlap])))
        lower = holdout_pred + low_q * scale.iloc[holdout].to_numpy()
        upper = holdout_pred + high_q * scale.iloc[holdout].to_numpy()
        coverage = float(np.mean((holdout_y >= lower) & (holdout_y <= upper)))
        evidence = min(max(dev_gain, 0), max(skill, 0), .25) / .25 * min(1., len(nonoverlap) / 12) * alpha
        evidence *= .75 if accuracy < always_up else 1.
        evidence = float(np.clip(evidence, 0, .8))
        current_scale = max(float(current.volatility_63.iloc[0]), .003) * np.sqrt(horizon)
        latest, fitted = _member_predictions(spec, scale, np.arange(len(x)), spec["current"], [current_scale])
        raw_log = float(sum(weights[name] * latest[name][0] for name in names))
        baseline_now = _baseline(y, np.arange(len(x)), baseline_name)
        predicted_log = alpha * raw_log + (1 - alpha) * baseline_now
        price = float(prices.Close.iloc[-1])
        importance = _importance(fitted, spec)
    rows = [{"Date": str(x.index[i].date()), "Actual return": float(np.expm1(y.iloc[i])),
             "Model return": float(np.expm1(p)), "Baseline return": float(np.expm1(b))}
            for i, p, b in zip(holdout, holdout_pred, base_test)]
    development = [{"Candidate": name, "Blended MAE": c["mae"], "Raw ML MAE": c["raw_mae"],
                    "ML share": c["alpha"], "Selected": name == selected} for name, c in candidates.items()]
    roles = spec.get("roles", []) if alpha else []
    return {
        "available": True, "model_version": VERSION, "horizon": horizon,
        "selected_candidate": selected, "candidate_comparison": development,
        "context_used": roles, "context_candidates": list(specs),
        "predicted_return": float(np.expm1(predicted_log)), "estimated_price": float(price * np.exp(predicted_log)),
        "score_forecast_return": float(np.expm1(predicted_log)), "raw_ml_return": float(np.expm1(raw_log)),
        "baseline_return": float(np.expm1(baseline_now)), "ml_blend": alpha,
        "forecast_kind": "Baseline fallback" if alpha == 0 else f"{selected} ML + baseline",
        "reason": ("Development tests did not justify ML over the baseline." if alpha == 0 else
                   f"{selected}; ML share {alpha:.0%}, selected before calibration and final testing."),
        "lower_return": float(np.expm1(predicted_log + low_q * current_scale)),
        "upper_return": float(np.expm1(predicted_log + high_q * current_scale)),
        "lower_price": float(price * np.exp(predicted_log + low_q * current_scale)),
        "upper_price": float(price * np.exp(predicted_log + high_q * current_scale)),
        "mae": mae, "baseline_mae": baseline_mae, "baseline": baseline_name,
        "directional_accuracy": accuracy, "always_up_accuracy": always_up,
        "baseline_directional_accuracy": float(np.mean(np.sign(base_test) == np.sign(holdout_y))),
        "nonoverlap_accuracy": nonoverlap_accuracy, "independent_windows": len(nonoverlap),
        "holdout_skill": float(skill), "development_skill": float(dev_gain),
        "interval_coverage": coverage, "nominal_coverage": .8, "evidence_weight": evidence, "weights": weights,
        "interval_residual_quantiles": {"lower": low_q, "upper": high_q},
        "development_errors": choice["errors"], "holdout_start": str(x.index[holdout[0]].date()),
        "holdout_end": str(x.index[holdout[-1]].date()), "calibration_start": str(x.index[cal[0]].date()),
        "calibration_end": str(x.index[cal[-1]].date()), "calibration_windows": len(range(0, len(cal), horizon)),
        "folds": fold_bounds, "calibration_folds": cal_bounds, "holdout_folds": test_bounds,
        "holdout_rows": len(holdout), "training_rows": len(x), "feature_importance": importance,
        "holdout_predictions": rows,
        "nonoverlap_mae": _metric(holdout_y[nonoverlap], holdout_pred[nonoverlap]),
        "nonoverlap_baseline_mae": _metric(holdout_y[nonoverlap], base_test[nonoverlap]),
        "trained_seconds": round(time.perf_counter() - start, 3), "as_of": str(prices.index[-1].date()),
    }


def short_history_forecast(prices, horizon=63, reason="Insufficient history for full validation", *, demo=False):
    """Limited-evidence estimates; never award research-score weight."""
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    data.require_price_source(prices, demo)
    c = prices.Close.astype(float)
    daily = np.log(c).diff()
    x = pd.DataFrame(index=c.index)
    for n in (5, 10, 21):
        x[f"momentum_{n}"] = np.log(c/c.shift(n))
    x["volatility"] = daily.rolling(21).std()
    x["distance"] = c/c.rolling(21).mean()-1
    y = np.log(c.shift(-horizon)/c)
    train = x.assign(target=y).replace([np.inf, -np.inf], np.nan).dropna()
    kind = "Statistical fallback (not ML)"
    note = "Too few matured horizon-specific examples for ML; shrunk historical daily-return estimate."
    # 63-session labels must have matured; no fabricated labels for new listings.
    prediction = float(np.clip(daily.tail(252).mean()*horizon*.25, -.35, .35))
    if len(train) >= 60 and np.isfinite(x.iloc[-1]).all():
        model = make_pipeline(StandardScaler(), Ridge(alpha=100))
        target = train.target.clip(train.target.quantile(.01), train.target.quantile(.99))
        model.fit(train[x.columns], target)
        raw_prediction = float(model.predict(x.iloc[[-1]])[0])
        blend = min(.25, len(train)/(12*horizon))
        prediction = float(np.clip(blend*raw_prediction+(1-blend)*prediction, -.7, .7))
        kind = "Short-history ML (unvalidated)"
        note = f"Ridge regression trained on {len(train)} matured labels; insufficient independent history for full validation; raw ML weight capped at 25%."
    return {"available": False, "predicted_return": float(np.expm1(prediction)),
            "estimated_price": float(c.iloc[-1]*np.exp(prediction)), "evidence_weight": 0.,
            "forecast_kind": kind, "reason": reason + ". " + note,
            "training_rows": len(train), "horizon": horizon, **_source_metadata(prices, demo)}
