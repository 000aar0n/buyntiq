"""A small, auditable forecasting ensemble with purged chronological validation.

Model selection, interval calibration, and final testing use distinct periods.
The final holdout is never used to choose model weights or interval width.
All forecasts are research estimates; accuracy is measured, not promised.
"""
import hashlib
import time
import threading
import numpy as np
import pandas as pd
from buyntiq import cache
from buyntiq.features import dataset, feature_frame

VERSION = "buyntiq-ensemble-4.0.1"
_training_lock = threading.RLock()
HORIZONS = {"1 month": 21, "3 months": 63, "6 months": 126}


def temporal_partitions(n, horizon):
    holdout_size = max(252, 2 * horizon)
    calibration_size = max(126, horizon)
    test_start = n - holdout_size
    cal_end = test_start - horizon
    cal_start = cal_end - calibration_size
    dev_end = cal_start - horizon
    if dev_end < max(500, 200 + 3*84 + horizon):
        raise ValueError("More price history is needed for separate training, calibration, and holdout periods.")
    return dict(dev=np.arange(dev_end), calibration=np.arange(cal_start, cal_end),
                holdout=np.arange(test_start, n), holdout_train=np.arange(test_start - horizon))


def _models():
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {
        "Ridge": make_pipeline(StandardScaler(), Ridge(alpha=40)),
        "Extra Trees": ExtraTreesRegressor(n_estimators=56, max_depth=6, min_samples_leaf=18,
                                           max_features=.8, n_jobs=1, random_state=42),
        "Gradient Boosting": HistGradientBoostingRegressor(max_iter=70, max_leaf_nodes=9,
                            l2_regularization=12, learning_rate=.045, min_samples_leaf=25,
                            early_stopping=False, random_state=42),
    }


def _fit_predict(model, x, y, scale, train, test):
    # Standardize by historical volatility to reduce price/regime dependence.
    target = y.iloc[train].to_numpy() / scale.iloc[train].to_numpy()
    lo, hi = np.quantile(target, [.01, .99])
    model.fit(x.iloc[train], np.clip(target, lo, hi))
    return np.clip(model.predict(x.iloc[test]), -8, 8) * scale.iloc[test].to_numpy()


def _metric(actual, predicted):
    return float(np.mean(np.abs(np.expm1(actual) - np.expm1(predicted))))


def error_skill(mae, baseline_mae):
    # A perfect baseline leaves no demonstrated improvement to claim.
    return 0.0 if baseline_mae <= 1e-9 else 1 - mae / baseline_mae


def forecast(prices, horizon=63):
    """Cache keyed by actual price content, horizon, and model version."""
    digest = hashlib.sha256(pd.util.hash_pandas_object(prices[["Close", "Volume"]], index=True).values.tobytes()).hexdigest()
    key = f"{VERSION}-{digest}-{horizon}"
    with cache.lock_for(key):
        saved = cache.read("models", key, 6 * 3600)
        if saved:
            return dict(saved, cache_hit=True)
        with _training_lock:
            result = _train(prices, horizon)
        cache.write("models", key, result)
        return dict(result, cache_hit=False)


def _train(prices, horizon):
    from sklearn.model_selection import TimeSeriesSplit
    from threadpoolctl import threadpool_limits
    start = time.perf_counter()
    x, y, scale, positions = dataset(prices, horizon)
    try:
        parts = temporal_partitions(len(x), horizon)
    except ValueError as exc:
        return {"available": False, "reason": str(exc), "model_version": VERSION}
    if x.empty or not np.isfinite(x.to_numpy()).all():
        return {"available": False, "reason": "Price history has missing or invalid model inputs.", "model_version": VERSION}
    names = list(_models())
    oof = {name: [] for name in names}
    actual, zero, drift = [], [], []
    fold_bounds = []
    splitter = TimeSeriesSplit(n_splits=3, test_size=84, gap=horizon)
    # One native thread avoids CPU oversubscription during portfolio analysis.
    with threadpool_limits(limits=1):
        for train, test in splitter.split(x.iloc[parts["dev"]]):
            # Every training label ends strictly before the test feature date.
            assert positions.iloc[train[-1]] + horizon < positions.iloc[test[0]]
            for name, model in _models().items():
                oof[name].extend(_fit_predict(model, x, y, scale, train, test))
            actual.extend(y.iloc[test])
            zero.extend(np.zeros(len(test)))
            drift.extend(np.repeat(float(y.iloc[train].tail(252).median()), len(test)))
            fold_bounds.append({"train_last": str(x.index[train[-1]].date()), "test_first": str(x.index[test[0]].date()), "test_last": str(x.index[test[-1]].date()), "gap_sessions": horizon})
        actual = np.asarray(actual)
        losses = {name: _metric(actual, np.asarray(values)) for name, values in oof.items()}
        baseline_losses = {"No price change": _metric(actual, np.asarray(zero)), "Historical median": _metric(actual, np.asarray(drift))}
        baseline_name = min(baseline_losses, key=baseline_losses.get)
        baseline_dev_mae = baseline_losses[baseline_name]
        # Weights come exclusively from development folds, never the holdout.
        raw_weights = {name: max(0.0, baseline_dev_mae - loss) for name, loss in losses.items()}
        if sum(raw_weights.values()) <= 1e-9:
            raw_weights = {name: float(name == min(losses, key=losses.get)) for name in names}
        weights = {name: weight / sum(raw_weights.values()) for name, weight in raw_weights.items()}
        dev_pred = sum(np.asarray(oof[name]) * weights[name] for name in names)
        dev_gain = error_skill(_metric(actual, dev_pred), baseline_dev_mae)

        cal, holdout, train_holdout = parts["calibration"], parts["holdout"], parts["holdout_train"]
        assert positions.iloc[parts["dev"][-1]] + horizon < positions.iloc[cal[0]]
        assert positions.iloc[train_holdout[-1]] + horizon < positions.iloc[holdout[0]]
        cal_pred = np.zeros(len(cal))
        holdout_pred = np.zeros(len(holdout))
        for name, model in _models().items():
            if weights[name] > 0:
                cal_pred += weights[name] * _fit_predict(model, x, y, scale, parts["dev"], cal)
                holdout_pred += weights[name] * _fit_predict(model, x, y, scale, train_holdout, holdout)
        # 80% empirical absolute-error interval, calibrated before the holdout.
        errors = np.abs(y.iloc[cal].to_numpy() - cal_pred) / scale.iloc[cal].to_numpy()
        quantile = min(1.0, np.ceil((len(errors) + 1) * .8) / len(errors))
        radius = float(np.quantile(errors, quantile, method="higher"))
        holdout_y = y.iloc[holdout].to_numpy()
        base_value = 0.0 if baseline_name == "No price change" else float(y.iloc[train_holdout].tail(252).median())
        base_pred = np.full(len(holdout), base_value)
        mae, baseline_mae = _metric(holdout_y, holdout_pred), _metric(holdout_y, base_pred)
        skill = error_skill(mae, baseline_mae)
        accuracy = float(np.mean(np.sign(holdout_pred) == np.sign(holdout_y)))
        always_up = float(np.mean(holdout_y > 0))
        nonoverlap = np.arange(0, len(holdout), horizon)
        nonoverlap_accuracy = float(np.mean(np.sign(holdout_pred[nonoverlap]) == np.sign(holdout_y[nonoverlap])))
        coverage = float(np.mean(np.abs(holdout_y - holdout_pred) <= radius * scale.iloc[holdout].to_numpy()))
        # This is a conservative heuristic evidence weight, NOT a probability.
        evidence = (min(max(dev_gain, 0), max(skill, 0), .25) / .25
                    * min(1.0, len(nonoverlap) / 12))
        evidence *= .75 if accuracy < always_up else 1.0
        evidence = float(np.clip(evidence, 0, .8))
        current = feature_frame(prices).iloc[[-1]]
        if current.isna().any(axis=None):
            return {"available": False, "reason": "Latest session has incomplete features.", "model_version": VERSION}
        current_scale = max(float(current.volatility_63.iloc[0]), .003) * np.sqrt(horizon)
        predictions, fitted = {}, {}
        for name, model in _models().items():
            if weights[name] <= 0:
                continue
            target = y.to_numpy() / scale.to_numpy()
            lo, hi = np.quantile(target, [.01, .99])
            model.fit(x, np.clip(target, lo, hi))
            predictions[name] = float(np.clip(model.predict(current)[0], -8, 8) * current_scale)
            fitted[name] = model
        raw_log = sum(predictions[n] * weights[n] for n in predictions)
        baseline_now = 0.0 if baseline_name == "No price change" else float(y.tail(252).median())
        # Blend only for the research score; the raw forecast remains visible.
        score_forecast = evidence * raw_log + (1-evidence) * baseline_now
        price = float(prices.Close.iloc[-1])
        importance = {}
        tree = fitted.get("Extra Trees")
        if tree is not None:
            importance = dict(sorted(zip(x.columns, map(float, tree.feature_importances_)), key=lambda item: item[1], reverse=True)[:6])
    predictions_rows = [{"Date": str(x.index[i].date()), "Actual return": float(np.expm1(y.iloc[i])),
                         "Model return": float(np.expm1(p)), "Baseline return": float(np.expm1(base_value))}
                        for i, p in zip(holdout, holdout_pred)]
    return {
        "available": True, "model_version": VERSION, "horizon": horizon,
        "predicted_return": float(np.expm1(raw_log)), "estimated_price": float(price*np.exp(raw_log)),
        "score_forecast_return": float(np.expm1(score_forecast)),
        "lower_return": float(np.expm1(raw_log-radius*current_scale)),
        "upper_return": float(np.expm1(raw_log+radius*current_scale)),
        "lower_price": float(price*np.exp(raw_log-radius*current_scale)), "upper_price": float(price*np.exp(raw_log+radius*current_scale)),
        "mae": mae, "baseline_mae": baseline_mae, "baseline": baseline_name,
        "directional_accuracy": accuracy, "always_up_accuracy": always_up,
        "nonoverlap_accuracy": nonoverlap_accuracy, "independent_windows": len(nonoverlap),
        "holdout_skill": float(skill), "development_skill": float(dev_gain),
        "interval_coverage": coverage, "nominal_coverage": .8,
        "evidence_weight": evidence, "weights": weights, "development_errors": losses,
        "holdout_start": str(x.index[holdout[0]].date()), "holdout_end": str(x.index[holdout[-1]].date()),
        "calibration_start": str(x.index[cal[0]].date()), "calibration_end": str(x.index[cal[-1]].date()),
        "folds": fold_bounds, "holdout_rows": len(holdout), "training_rows": len(x),
        "feature_importance": importance, "holdout_predictions": predictions_rows,
        "trained_seconds": round(time.perf_counter()-start, 3), "as_of": str(prices.index[-1].date()),
    }
