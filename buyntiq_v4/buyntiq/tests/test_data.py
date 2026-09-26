from buyntiq import cache, data


def test_live_cache_roundtrip_and_stale_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr(cache,"ROOT",tmp_path)
    class Provider:
        def history(self,**kwargs):
            return data.demo_prices("AAPL").copy()
    monkeypatch.setattr(data,"_stock",lambda _:Provider())
    frame=data.prices("AAPL")
    saved=data.prices("AAPL")
    assert saved.Close.iloc[-1] == frame.Close.iloc[-1]
    assert not saved.attrs["demo"]
    class Unavailable:
        def history(self,**kwargs):
            raise TimeoutError("Provider unavailable")
    monkeypatch.setattr(data,"_stock",lambda _:Unavailable())
    stale=data.prices("AAPL",refresh=True)
    assert stale.attrs["stale"]
    assert stale.Close.iloc[-1] == frame.Close.iloc[-1]


def test_quote_currency_is_distinct_from_financial_currency(tmp_path,monkeypatch):
    monkeypatch.setattr(cache,"ROOT",tmp_path)
    class Provider:
        def get_info(self):
            return {"shortName":"Example","currency":"USD","financialCurrency":"EUR", "debtToEquity":-20,"profitMargins":.1}
    monkeypatch.setattr(data,"_stock",lambda _:Provider())
    result=data.company("TEST")
    assert result["currency"] == "USD"
    assert result["financial_currency"] == "EUR"
    assert result["debt_to_equity"] is None
    assert result["coverage"] == 1


def test_missing_prices_never_silently_become_demo_data(tmp_path,monkeypatch):
    import pytest
    monkeypatch.setattr(cache,"ROOT",tmp_path)
    class Provider:
        def history(self,**kwargs):
            raise TimeoutError("Provider unavailable")
    monkeypatch.setattr(data,"_stock",lambda _:Provider())
    with pytest.raises(ValueError,match="No price history"):
        data.prices("AAPL")
