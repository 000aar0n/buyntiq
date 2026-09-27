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

VERSION = "buyntiq-ensemble-5.0.0"
_training_lock = threading.RLock()
HORIZONS = {"1 month": 21, "3 months": 63, "6 months": 126, "1 year": 252}


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


def _choose_blend(actual, model_pred, baseline_pred, fold_sizes):
    """Development-only choice; baseline wins near ties and unstable gains."""
    actual, model_pred, baseline_pred = map(np.asarray, (actual, model_pred, baseline_pred))
    base_loss = _metric(actual, baseline_pred)
    if base_loss <= 1e-9:
        return 0.0
    candidates = []
    cuts = np.cumsum([0] + list(fold_sizes))
    for alpha in (0., .25, .5, .75, 1.):
        pred = alpha*model_pred + (1-alpha)*baseline_pred
        loss = _metric(actual, pred)
        wins = sum(_metric(actual[a:b], pred[a:b]) < _metric(actual[a:b], baseline_pred[a:b]) for a,b in zip(cuts[:-1],cuts[1:]))
        if alpha == 0 or (loss < .97*base_loss and wins >= 2):
            candidates.append((alpha,loss))
    best = min(loss for _,loss in candidates)
    return min(alpha for alpha,loss in candidates if loss <= best*1.02+1e-12)


def _walk_predictions(x, y, scale, positions, tests, horizon, weights, baseline_name):
    """Expanding training with quarterly-or-horizon refits and matured labels."""
    predictions, baselines, bounds = [], [], []
    step = max(63, horizon)
    for offset in range(0,len(tests),step):
        block = tests[offset:offset+step]
        train = np.flatnonzero(positions.to_numpy()+horizon < positions.iloc[block[0]])
        assert len(train) and positions.iloc[train[-1]]+horizon < positions.iloc[block[0]]
        pred = np.zeros(len(block))
        for name, model in _models().items():
            if weights[name] > 0:
                pred += weights[name]*_fit_predict(model,x,y,scale,train,block)
        base = 0. if baseline_name == "No price change" else float(y.iloc[train].tail(252).median())
        predictions.extend(pred)
        baselines.extend(np.repeat(base,len(block)))
        bounds.append({"train_last":str(x.index[train[-1]].date()),"test_first":str(x.index[block[0]].date()),"test_last":str(x.index[block[-1]].date()),"gap_sessions":horizon})
    return np.asarray(predictions), np.asarray(baselines), bounds


def _train(prices, horizon):
    from sklearn.model_selection import TimeSeriesSplit
    from threadpoolctl import threadpool_limits
    start = time.perf_counter()
    if horizon not in HORIZONS.values():
        raise ValueError("Supported horizons are 21, 63, 126, and 252 sessions.")
    x,y,scale,positions = dataset(prices,horizon)
    try:
        parts = temporal_partitions(len(x),horizon)
    except ValueError as exc:
        return {"available":False,"reason":str(exc),"model_version":VERSION}
    if x.empty or not np.isfinite(x.to_numpy()).all():
        return {"available":False,"reason":"Invalid historical features.","model_version":VERSION}
    current = feature_frame(prices).iloc[[-1]]
    if not np.isfinite(current.to_numpy()).all():
        return {"available":False,"reason":"Latest session has incomplete features.","model_version":VERSION}
    names = list(_models())
    oof = {name:[] for name in names}
    actual,zero,drift,fold_bounds,fold_sizes = [],[],[],[],[]
    # Longer development blocks increase the number of distinct forecast outcomes.
    test_size = min(max(84,horizon), (len(parts['dev'])-horizon-200)//3)
    splitter = TimeSeriesSplit(n_splits=3,test_size=test_size,gap=horizon)
    with threadpool_limits(limits=1):
        for train,test in splitter.split(x.iloc[parts['dev']]):
            assert positions.iloc[train[-1]]+horizon < positions.iloc[test[0]]
            for name,model in _models().items():
                oof[name].extend(_fit_predict(model,x,y,scale,train,test))
            actual.extend(y.iloc[test]); zero.extend(np.zeros(len(test)))
            drift.extend(np.repeat(float(y.iloc[train].tail(252).median()),len(test)))
            fold_sizes.append(len(test))
            fold_bounds.append({"train_last":str(x.index[train[-1]].date()),"test_first":str(x.index[test[0]].date()),"test_last":str(x.index[test[-1]].date()),"gap_sessions":horizon})
        actual = np.asarray(actual)
        losses = {n:_metric(actual,np.asarray(v)) for n,v in oof.items()}
        bases = {"No price change":np.asarray(zero),"Historical median":np.asarray(drift)}
        baseline_name = min(bases,key=lambda n:_metric(actual,bases[n]))
        baseline_dev = bases[baseline_name]
        # Smooth inverse-error weights rather than unstable winner-takes-all gains.
        inverse = {n:1/max(loss,1e-4) for n,loss in losses.items()}
        weights = {n:(.5/len(names)+.5*inverse[n]/sum(inverse.values())) for n in names}
        raw_dev = sum(weights[n]*np.asarray(oof[n]) for n in names)
        alpha = _choose_blend(actual,raw_dev,baseline_dev,fold_sizes)
        dev_pred = alpha*raw_dev+(1-alpha)*baseline_dev
        dev_gain = error_skill(_metric(actual,dev_pred),_metric(actual,baseline_dev))
        cal,holdout = parts['calibration'],parts['holdout']
        raw_cal,base_cal,cal_bounds = _walk_predictions(x,y,scale,positions,cal,horizon,weights,baseline_name)
        raw_test,base_test,test_bounds = _walk_predictions(x,y,scale,positions,holdout,horizon,weights,baseline_name)
        cal_pred = alpha*raw_cal+(1-alpha)*base_cal
        holdout_pred = alpha*raw_test+(1-alpha)*base_test
        # Signed errors preserve asymmetric upside/downside tails. This is an
        # empirical interval with measured coverage, not an iid conformal claim.
        residuals = (y.iloc[cal].to_numpy()-cal_pred)/scale.iloc[cal].to_numpy()
        low_q = min(0.,float(np.quantile(residuals,.1,method="lower")))
        high_q = max(0.,float(np.quantile(residuals,.9,method="higher")))
        holdout_y = y.iloc[holdout].to_numpy()
        mae,baseline_mae = _metric(holdout_y,holdout_pred),_metric(holdout_y,base_test)
        skill = error_skill(mae,baseline_mae)
        accuracy = float(np.mean(np.sign(holdout_pred)==np.sign(holdout_y)))
        always_up = float(np.mean(holdout_y>0))
        nonoverlap = np.arange(0,len(holdout),horizon)
        nonoverlap_accuracy = float(np.mean(np.sign(holdout_pred[nonoverlap])==np.sign(holdout_y[nonoverlap])))
        lower = holdout_pred+low_q*scale.iloc[holdout].to_numpy()
        upper = holdout_pred+high_q*scale.iloc[holdout].to_numpy()
        coverage = float(np.mean((holdout_y>=lower)&(holdout_y<=upper)))
        evidence = min(max(dev_gain,0),max(skill,0),.25)/.25*min(1.,len(nonoverlap)/12)*alpha
        evidence *= .75 if accuracy < always_up else 1.
        evidence = float(np.clip(evidence,0,.8))
        current_scale = max(float(current.volatility_63.iloc[0]),.003)*np.sqrt(horizon)
        predictions,fitted = {},{}
        target = y.to_numpy()/scale.to_numpy()
        lo,hi = np.quantile(target,[.01,.99])
        for name,model in _models().items():
            model.fit(x,np.clip(target,lo,hi))
            predictions[name] = float(np.clip(model.predict(current)[0],-8,8)*current_scale)
            fitted[name] = model
        raw_log = sum(weights[n]*predictions[n] for n in names)
        baseline_now = 0. if baseline_name=="No price change" else float(y.tail(252).median())
        predicted_log = alpha*raw_log+(1-alpha)*baseline_now
        price = float(prices.Close.iloc[-1])
        importance = dict(sorted(zip(x.columns,map(float,fitted['Extra Trees'].feature_importances_)),key=lambda item:item[1],reverse=True)[:6])
    rows = [{"Date":str(x.index[i].date()),"Actual return":float(np.expm1(y.iloc[i])),"Model return":float(np.expm1(p)),"Baseline return":float(np.expm1(b))} for i,p,b in zip(holdout,holdout_pred,base_test)]
    return {
        "available":True,"model_version":VERSION,"horizon":horizon,
        "predicted_return":float(np.expm1(predicted_log)),"estimated_price":float(price*np.exp(predicted_log)),
        "score_forecast_return":float(np.expm1(predicted_log)),"raw_ml_return":float(np.expm1(raw_log)),
        "baseline_return":float(np.expm1(baseline_now)),"ml_blend":alpha,
        "forecast_kind":"Baseline fallback" if alpha==0 else "Baseline-blended ML",
        "reason":("Development tests did not justify ML over the baseline." if alpha==0 else f"ML share {alpha:.0%}, chosen before calibration and final testing."),
        "lower_return":float(np.expm1(predicted_log+low_q*current_scale)),"upper_return":float(np.expm1(predicted_log+high_q*current_scale)),
        "lower_price":float(price*np.exp(predicted_log+low_q*current_scale)),"upper_price":float(price*np.exp(predicted_log+high_q*current_scale)),
        "mae":mae,"baseline_mae":baseline_mae,"baseline":baseline_name,
        "directional_accuracy":accuracy,"always_up_accuracy":always_up,"nonoverlap_accuracy":nonoverlap_accuracy,
        "independent_windows":len(nonoverlap),"holdout_skill":float(skill),"development_skill":float(dev_gain),
        "interval_coverage":coverage,"nominal_coverage":.8,"evidence_weight":evidence,"weights":weights,
        "development_errors":losses,"holdout_start":str(x.index[holdout[0]].date()),"holdout_end":str(x.index[holdout[-1]].date()),
        "calibration_start":str(x.index[cal[0]].date()),"calibration_end":str(x.index[cal[-1]].date()),
        "folds":fold_bounds,"calibration_folds":cal_bounds,"holdout_folds":test_bounds,"holdout_rows":len(holdout),
        "training_rows":len(x),"feature_importance":importance,"holdout_predictions":rows,
        "nonoverlap_mae":_metric(holdout_y[nonoverlap],holdout_pred[nonoverlap]),
        "nonoverlap_baseline_mae":_metric(holdout_y[nonoverlap],base_test[nonoverlap]),
        "trained_seconds":round(time.perf_counter()-start,3),"as_of":str(prices.index[-1].date()),
    }


def short_history_forecast(prices, horizon=63, reason="Insufficient history for full validation"):
    """Limited-evidence estimates; never award research-score weight."""
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
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
            "training_rows": len(train), "horizon": horizon}
