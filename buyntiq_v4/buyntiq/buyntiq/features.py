"""Causal features: each row uses prices available through that session only."""
import numpy as np
import pandas as pd


def feature_frame(prices):
    c = prices.Close.astype(float)
    daily = np.log(c).diff()
    f = pd.DataFrame(index=c.index)
    for n in (5, 21, 63, 126):
        f[f"momentum_{n}"] = np.log(c / c.shift(n))
    for n in (20, 50, 200):
        f[f"distance_ma_{n}"] = c / c.rolling(n).mean() - 1
    for n in (21, 63):
        f[f"volatility_{n}"] = daily.rolling(n).std()
    f["volatility_ratio"] = f.volatility_21 / f.volatility_63.clip(lower=.001)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    f["rsi"] = (gain / (gain + loss).replace(0, np.nan)).fillna(.5)
    f["drawdown_126"] = c / c.rolling(126).max() - 1
    f["trend_acceleration"] = f.momentum_21 - f.momentum_63 / 3
    volume = prices.Volume.astype(float).fillna(0)
    f["relative_volume"] = (volume / volume.rolling(21).mean().replace(0, np.nan)).fillna(1).clip(0, 10)
    f["volume_trend"] = (volume.rolling(5).mean() / volume.rolling(63).mean().replace(0, np.nan)).fillna(1).clip(0, 10)
    f["downside_volatility"] = daily.clip(upper=0).rolling(63).std()
    f["return_autocorrelation"] = daily.rolling(63).corr(daily.shift(1)).fillna(0)
    return f.replace([np.inf, -np.inf], np.nan)


def dataset(prices, horizon):
    f = feature_frame(prices)
    target = np.log(prices.Close.shift(-horizon) / prices.Close)
    scale = f.volatility_63.clip(lower=.003) * np.sqrt(horizon)
    # Keeping the original integer position makes temporal boundaries auditable.
    joined = f.assign(_target=target, _scale=scale, _position=np.arange(len(prices))).dropna()
    return joined[list(f.columns)], joined._target, joined._scale, joined._position
