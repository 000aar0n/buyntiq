# Buyntiq model and scoring notes

Version: `buyntiq-ensemble-4.0.1`. This is an inspectable research model, not a verified market-beating strategy.

## Inputs and target

- One stock at a time; up to ten years of adjusted daily prices and volume.
- Causal momentum (5/21/63/126 sessions), distances from 20/50/200-session averages, 21/63-session volatility, volatility ratio, RSI, 126-session drawdown, trend acceleration, relative volume, volume trend, downside volatility, and return autocorrelation.
- Target: `log(close[t+h] / close[t])`, for `h = 21, 63, 126` observed trading sessions.
- Models fit the target divided by trailing 63-session volatility times `sqrt(h)`. A volatility floor stabilizes quiet histories. Training targets are clipped at their training-only 1st/99th percentiles; test labels are not clipped. Standardization is fitted inside each Ridge training pipeline.
- Contemporary company metrics influence the research score separately. They are not backfilled into historical ML features. News is context only and does not influence the score.

## Evaluation chronology

1. Reserve a final holdout of `max(252, 2h)` labeled rows.
2. Leave `h` unused rows before that holdout, then reserve a calibration block of `max(126, h)` rows.
3. Leave another `h` rows before calibration. The earlier rows form development.
4. Development uses three expanding time-series folds with 84-row validation blocks and `gap=h`. There are at least 200 rows in the earliest training fold. Assertions verify that each final training label ends strictly before the next validation feature date.
5. Compare Ridge, Extra Trees, and Histogram Gradient Boosting against a zero-return baseline and a historical median-return baseline. The median uses the latest 252 available training labels. Select the baseline with the lower development MAE.
6. Ensemble weights are proportional to each candidate's positive development MAE improvement over that baseline. Candidates with no improvement receive zero weight. If none improves, display the lowest-error candidate but do not let it influence the score unless the development gate is positive (normally it cannot be).
7. Refit the selected ensemble on development to predict the later calibration block. The approximately 80th percentile of absolute volatility-scaled residuals sets the empirical interval radius.
8. Refit on every eligible label before the holdout (still with a horizon gap) and test once on the holdout. Weights and interval radius remain frozen. Report mean absolute return error, direction accuracy, and interval coverage.
9. Refit the selected ensemble on all currently known labels for the current forecast. Display the raw ensemble estimate and its empirical interval. The final refit has more data than the historical evaluation model; future performance can differ.

The initial development periods overlap in their training rows, and daily horizon-return labels overlap in evaluation. The UI therefore also reports direction accuracy on non-overlapping horizon-spaced starts. For the default three-month model, a 252-row holdout contains only **four** such windows. Neither the interval nor the direction accuracy is a calibrated probability of future performance.

## Models

- Ridge with StandardScaler, alpha 40.
- Extra Trees: 56 trees, depth 6, leaf minimum 18, feature fraction 0.8.
- Histogram Gradient Boosting: 70 iterations, up to 9 leaves, learning rate 0.045, L2 regularization 12, leaf minimum 25, random early stopping disabled.
- Fixed seeds and bounded native thread counts. No broad hyperparameter search over the final holdout.

## Evidence gate

Let development and holdout skill be `1 − ensemble MAE / selected-baseline MAE` on simple holding-period returns. Define:

```text
evidence = min(max(dev_skill, 0), max(holdout_skill, 0), 0.25) / 0.25
           × min(1, non_overlapping_windows / 12)
```

Reduce by 25% if holdout direction accuracy is below always-up accuracy; bound the result to `[0, 0.8]`. This is a conservative heuristic **weight**, not a probability or confidence level. It goes to zero when either error skill is nonpositive.

This gate uses the final holdout. Therefore that same holdout is **not** an independent validation of the entire scoring policy after gating. A subsequent untouched period or paper-trading study is needed for that claim. The comparison baseline was selected in development, and it need not be the strongest baseline in the holdout.

## Research score

Base weights are 0.55 technical and 0.45 company. Company weight is multiplied by the fraction of six available financial metrics. Missing company values are omitted. The retained fundamental heuristics use growth, profitability, valuation, leverage, and the sign of cash flow. Ratios are not sector-normalized; these choices are not optimized predictive coefficients.

`ML weight = 0.25 × evidence`; remaining weight is distributed over available base components. The ML component is `50 + 50 × tanh(raw forecast return / 0.20)`. Final score is the weighted sum on a 0–100 scale. The same formula is used for research, candidate ranking, and portfolio review.

## Portfolio methodology

- Technical screening precedes expensive full analysis. Stocks outside the shortlist do not receive a final score; missed candidates are a real limitation.
- Highest scores selects the best final scores in the analyzed shortlist without correlation substitutions.
- Diversified mode subtracts up to 12 points for positive correlation to selected holdings and 4 points per selected stock in the same sector. This is an explicit heuristic, not an efficient-frontier optimizer.
- Allocation raw weight is `(max(score,10)/100) / max(volatility,0.10)^profile_power`, followed by normalization with feasible position caps.
- Covariance risk uses Ledoit–Wolf shrinkage on up to 252 aligned daily returns. No forecast-return estimate is treated as a known expected return.

## Practical limits

The interval radius is calibrated on a short historical block. Temporal dependence, overlapping labels, volatility changes, and distribution shifts invalidate independent-sample coverage guarantees. Measured coverage can be substantially below 80%.

There is no point-in-time fundamentals dataset, survivorship-free universe, historical selection backtest, slippage, trading costs, taxes, or execution model. Data can be delayed, revised, split-adjusted, incomplete, or blocked. Newly listed companies may lack sufficient history. Negative debt/equity ratios are omitted rather than treated as low leverage.

The first requested stock trains three candidate models across three development folds, then calibration, holdout, and final fits. The first portfolio can take longer because multiple tickers need data and models. Cached reruns avoid repeating that work. Homepage/navigation performance is independent of live requests.

Synthetic demo data is deterministic and explicitly marked. It validates the software flow only. No real accuracy or expected return conclusion should be drawn from demo metrics.

## Primary technical references

- [Streamlit page navigation](https://docs.streamlit.io/develop/api-reference/navigation/st.navigation)
- [scikit-learn TimeSeriesSplit and gap](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- [scikit-learn LedoitWolf covariance](https://scikit-learn.org/stable/modules/generated/sklearn.covariance.LedoitWolf.html)
- [yfinance download API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
