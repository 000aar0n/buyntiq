import numpy as np
import pandas as pd
import pytest
from buyntiq import data
from buyntiq.features import feature_frame, dataset
from buyntiq.model import temporal_partitions, forecast
from buyntiq.analytics import combined_score, analyze
from buyntiq.portfolio import capped_weights, choose_holdings, normalize_entries, build, review


def test_features_do_not_read_future_prices():
    original = data.demo_prices("AAPL").copy()
    changed = original.copy()
    changed.loc[changed.index[1500]:, "Close"] *= 3
    pd.testing.assert_frame_equal(feature_frame(original).iloc[:1500],feature_frame(changed).iloc[:1500])
    pd.testing.assert_frame_equal(feature_frame(original).iloc[:1000],feature_frame(original.iloc[:1000]))


@pytest.mark.parametrize("horizon",[21,63,126])
def test_partition_label_end_is_before_next_feature_date(horizon):
    frame = data.demo_prices("MSFT")
    x,y,scale,positions = dataset(frame,horizon)
    parts = temporal_partitions(len(x),horizon)
    for train,test in [(parts["dev"],parts["calibration"]),(parts["holdout_train"],parts["holdout"])]:
        assert positions.iloc[train[-1]] + horizon < positions.iloc[test[0]]
    assert parts["calibration"][-1] < parts["holdout"][0]
    assert positions.iloc[-1] + horizon == len(frame)-1


def test_weak_ml_has_no_score_influence_and_missing_company_is_not_zero():
    score,components = combined_score(72,None,0,{"available":True,"evidence_weight":0,"predicted_return":4})
    assert score == 72
    assert components == {"Technical":{"score":72.,"weight":1.}}
    score,components = combined_score(70,60,6,{"available":True,"evidence_weight":.4,"predicted_return":.1})
    assert sum(v["weight"] for v in components.values()) == pytest.approx(1)
    assert components["ML · 3 months"]["weight"] == pytest.approx(.1)


def test_perfect_baseline_does_not_fabricate_model_skill():
    from buyntiq.model import error_skill
    assert error_skill(0,0) == 0
    assert error_skill(.02,0) == 0
    assert error_skill(.08,.1) == pytest.approx(.2)


@pytest.mark.parametrize("raw,cap",[([100,1,1,1,1],.25),([5,1],.25),([1],.2),([1,2,3,4,5,6],.35)])
def test_allocation_respects_sum_and_feasible_cap(raw,cap):
    weights,effective = capped_weights(raw,cap)
    assert weights.sum() == pytest.approx(1)
    assert np.all(weights>0)
    assert np.all(weights<=effective+1e-10)
    assert effective == max(cap,1/len(raw))


def test_highest_scores_cannot_substitute_a_lower_score():
    candidates=[{"symbol":"A","score":92},{"symbol":"B","score":85},{"symbol":"C","score":65}]
    assert [r["symbol"] for r in choose_holdings(candidates[::-1],2,"Highest scores")] == ["A","B"]


def test_holdings_duplicates_fractional_shares_and_invalid_values():
    assert normalize_entries(pd.DataFrame({"Ticker":["aapl","AAPL","BRK.B"],"Shares":[2.5,3,.2]})) == {"AAPL":5.5,"BRK-B":.2}
    for shares in [0,-1,float("inf"),float("nan")]:
        with pytest.raises(ValueError):
            normalize_entries(pd.DataFrame({"Ticker":["AAPL"],"Shares":[shares]}))


def test_price_shapes_are_normalized_without_cross_ticker_contamination():
    original=data.demo_prices("AAPL").head(5)
    multi=pd.concat({"AAPL":original,"MSFT":original*2},axis=1)
    for frame in (multi,multi.swaplevel(0,1,axis=1)):
        got=data.clean_prices(frame,"AAPL")
        pd.testing.assert_series_equal(got.Close,original.Close,check_freq=False)


def test_cached_model_and_analysis_are_consistent():
    frame=data.demo_prices("AAPL")
    first=forecast(frame,63)
    second=forecast(frame,63)
    assert first["available"] and second["cache_hit"]
    assert first["predicted_return"] == second["predicted_return"]
    assert first["lower_return"] <= first["predicted_return"] <= first["upper_return"]
    assert 0 <= first["interval_coverage"] <= 1
    assert first["independent_windows"] == 4
    assert sum(first["weights"].values()) == pytest.approx(1)
    analysis=analyze("AAPL",demo=True)
    assert analysis["forecast"]["predicted_return"] == first["predicted_return"]
    # For unsupported listings, return an explicit reason rather than a made-up model.
    assert not forecast(frame.tail(400),63)["available"]


def test_demo_portfolios_math_and_final_ranking():
    result=build(["AAPL","MSFT","NVDA","AMD"],count=3,finalists=4,budget=10000,demo=True)
    table=result["table"]
    assert table.Weight.sum() == pytest.approx(1)
    assert table.Ticker.tolist() == result["ranking"].head(3).Ticker.tolist()
    spent=float((table["Whole shares"]*table.Price).sum())
    assert spent+result["cash"] == pytest.approx(10000)
    assert result["risk"]["volatility"] > 0
    rated=review({"AAPL":2.5,"MSFT":3},demo=True)
    t=rated["table"]
    assert rated["total"] == pytest.approx((t.Shares*t.Price).sum())
    assert t.Weight.sum() == pytest.approx(1)
    assert rated["score"] == pytest.approx((t.Weight*t.Score).sum())


def test_partial_review_excludes_unpriced_holdings(monkeypatch):
    real=analyze("AAPL",True)
    def stub(symbol,*args,**kwargs):
        if symbol == "FAIL":
            raise ValueError("Provider unavailable")
        return real
    monkeypatch.setattr("buyntiq.portfolio.analyze",stub)
    result=review({"AAPL":2,"FAIL":10},demo=True)
    assert result["table"].Ticker.tolist() == ["AAPL"]
    assert result["table"].Weight.iloc[0] == 1
    assert result["errors"] == {"FAIL":"Provider unavailable"}


def test_company_low_pe_branch_and_negative_debt():
    from buyntiq.legacy_calculations import fundamental_score
    assert fundamental_score({"forward_pe":5})[0] == 65
    assert fundamental_score({"debt_to_equity":-1})[0] is None
