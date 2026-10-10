import numpy as np
import pandas as pd
import pytest
from buyntiq import data
from buyntiq.analytics import analyze
from buyntiq.model import short_history_forecast
from buyntiq.portfolio import build


def test_directory_sector_is_preserved(monkeypatch):
    monkeypatch.setattr(data.cache, 'read', lambda *a: None)
    assert data.resolve_sector('NEW', 'N/A', 'Finance') == ('Financial Services', 'Listing directory')
    assert data.resolve_sector('AAPL', 'Unknown')[0] == 'Technology'
    assert data.resolve_sector('NEW', 'Energy', 'Finance')[0] == 'Energy'


def test_short_history_ml_and_statistical_fallback():
    frame = data.demo_prices('MSFT')
    for n, method in [(63, 'Statistical'), (400, 'Short-history ML')]:
        result = short_history_forecast(frame.tail(n), demo=True)
        assert result['forecast_kind'].startswith(method)
        assert np.isfinite(result['predicted_return'])
        assert result['evidence_weight'] == 0
        assert result['estimated_price'] == pytest.approx(frame.Close.iloc[-1]*(1+result['predicted_return']))
    # Changing unknown future rows cannot enter the supplied training sample.
    assert short_history_forecast(frame.tail(400), demo=True)['training_rows'] == 400-21-63


def test_analysis_fills_forecast_without_granting_ml_score():
    r = analyze('MSFT', demo=True, history=data.demo_prices('MSFT').tail(400))
    assert np.isfinite(r['forecast']['predicted_return'])
    assert 'ML · 3 months' not in r['components']


def test_builder_sector_hints_and_forecasts(monkeypatch):
    from buyntiq import portfolio
    real = analyze('MSFT', demo=True, history=data.demo_prices('MSFT').tail(400))
    real['company']['sector'] = 'Unknown'
    monkeypatch.setattr(portfolio, 'analyze', lambda *a, **k: real)
    result = build(['MSFT'], count=1, demo=True, sector_hints={'MSFT':'Finance'}, positive_only=False)
    row = result['table'].iloc[0]
    assert row.Sector == 'Financial Services'
    assert row['Forecast method'] == 'Short-history ML (unvalidated)'
    assert np.isfinite(row['ML forecast'])


def test_positive_filter_and_cash_projection():
    from buyntiq.portfolio import positive_forecast, projected_portfolio
    for v in [-.2, 0, None, float('nan'), float('inf')]:
        assert not positive_forecast({'forecast': {'predicted_return':v}})
    assert positive_forecast({'forecast': {'predicted_return':.01}})
    t = pd.DataFrame({'Weight':[.5,.5], 'Price':[300.,200.], 'Shares':[1,2], 'ML forecast':[.1,.2]})
    whole = projected_portfolio(t,1000,True)
    assert whole['gain'] == pytest.approx(110)
    assert whole['return'] == pytest.approx(.11)
    assert whole['cash'] == 300
    assert whole['end_value'] == 1110
    assert projected_portfolio(t,1000)['return'] == pytest.approx(.15)
    percent = projected_portfolio(t, 0)
    assert percent['return'] == pytest.approx(.15)
    assert percent['gain'] == percent['end_value'] == 0
    t.loc[0,'ML forecast'] = float('nan')
    assert not projected_portfolio(t,1000)['available']
    assert not projected_portfolio(t,0)['available']


def test_builder_excludes_negative_forecast_even_with_high_score(monkeypatch):
    from copy import deepcopy
    from buyntiq import portfolio
    real = analyze('MSFT', demo=True)
    def stub(symbol,*args,**kwargs):
        x = deepcopy(real)
        x['symbol'] = symbol
        x['score'] = 99 if symbol == 'AAPL' else 60
        x['forecast']['predicted_return'] = -.1 if symbol == 'AAPL' else .05
        return x
    monkeypatch.setattr(portfolio, 'analyze', stub)
    result = build(['AAPL','MSFT'], count=2, demo=True)
    assert result['table'].Ticker.tolist() == ['MSFT']
    assert result['ranking'].iloc[0]['Eligibility'].startswith('Excluded')
    with pytest.raises(ValueError, match='No analyzed candidates'):
        build(['AAPL'], count=1, demo=True)

@pytest.mark.parametrize('horizon', [21,63,126,252])
def test_actual_forecast_horizons(horizon):
    r = analyze('MSFT', demo=True, horizon=horizon)
    assert r['forecast']['horizon'] == horizon
    assert np.isfinite(r['forecast']['predicted_return'])
    if r['forecast']['available']:
        assert all(f['gap_sessions'] == horizon for f in r['forecast']['folds'])


def test_unknown_sector_and_missing_data_are_excluded():
    from copy import deepcopy
    from buyntiq.portfolio import recommendation_exclusion
    base = analyze('MSFT', demo=True)
    base['forecast']['predicted_return'] = .1
    assert recommendation_exclusion(base) is None
    x = deepcopy(base)
    x['company']['sector'] = 'N/A'
    assert 'unknown sector' in recommendation_exclusion(x)
    x = deepcopy(base)
    x['fundamental_score'] = None
    assert 'financial' in recommendation_exclusion(x)


def test_full_scan_crosses_old_100_stock_limit(monkeypatch):
    from copy import deepcopy
    from buyntiq import portfolio
    seen = []
    frame = data.demo_prices('MSFT')
    def batches(symbols, *a):
        seen.extend(symbols)
        assert len(symbols) <= 64
        return {s:frame for s in symbols}, {}
    real = analyze('MSFT', demo=True)
    def stub(s,*a,**kw):
        x = deepcopy(real)
        x['symbol'] = s
        x['forecast']['predicted_return'] = .1
        return x
    monkeypatch.setattr(data, 'batch_prices', batches)
    monkeypatch.setattr(portfolio, 'analyze', stub)
    symbols = ['T'+str(i) for i in range(137)]
    result = build(symbols,count=3,finalists=6)
    assert seen == symbols
    assert result['screened'] == 137
    # The current builder fully analyzes at least 30 finalists when available.
    assert result['analyzed'] == 30
    assert result['universe_count'] == 137
