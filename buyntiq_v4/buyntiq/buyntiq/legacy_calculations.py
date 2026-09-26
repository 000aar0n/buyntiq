"""Core financial calculations retained from the supplied Buyntiq app."""

import numpy as np
import pandas as pd
from buyntiq.data import normalize_symbol as normalize_yahoo_symbol

def safe_float(value):
    try:
        if value is None:
            return None
        value = float(value)
        return value if np.isfinite(value) else None
    except Exception:
        return None

def clamp(value, low=0.0, high=100.0):
    return float(np.clip(value, low, high))

def _clean_price_frame(data, ticker=None):
    """
    Normalize the several DataFrame shapes yfinance can return.

    yfinance may return:
      - normal columns: Close, Open, High, Low, Volume
      - MultiIndex: (Price, Ticker)
      - MultiIndex: (Ticker, Price)

    v10 assumed normal columns for single-ticker downloads, which caused
    valid symbols to appear "missing" when yfinance returned a MultiIndex.
    """
    if data is None or not isinstance(data, pd.DataFrame) or data.empty:
        return None
    frame = data
    if isinstance(frame.columns, pd.MultiIndex):
        ticker_upper = str(ticker).upper() if ticker else None
        level0 = [str(x).upper() for x in frame.columns.get_level_values(0)]
        level1 = [str(x).upper() for x in frame.columns.get_level_values(1)]
        if ticker_upper and ticker_upper in level0:
            try:
                frame = frame.xs(ticker, axis=1, level=0, drop_level=True)
            except Exception:
                matching = [x for x in frame.columns.get_level_values(0).unique() if str(x).upper() == ticker_upper]
                if matching:
                    frame = frame.xs(matching[0], axis=1, level=0, drop_level=True)
        elif ticker_upper and ticker_upper in level1:
            try:
                frame = frame.xs(ticker, axis=1, level=1, drop_level=True)
            except Exception:
                matching = [x for x in frame.columns.get_level_values(1).unique() if str(x).upper() == ticker_upper]
                if matching:
                    frame = frame.xs(matching[0], axis=1, level=1, drop_level=True)
        else:
            price_names = {'OPEN', 'HIGH', 'LOW', 'CLOSE', 'ADJ CLOSE', 'VOLUME'}
            unique0 = {str(x).upper() for x in frame.columns.get_level_values(0).unique()}
            unique1 = {str(x).upper() for x in frame.columns.get_level_values(1).unique()}
            if unique0.intersection(price_names):
                try:
                    frame = frame.copy(deep=False)
                    frame.columns = frame.columns.get_level_values(0)
                except Exception:
                    return None
            elif unique1.intersection(price_names):
                try:
                    frame = frame.copy(deep=False)
                    frame.columns = frame.columns.get_level_values(1)
                except Exception:
                    return None
            else:
                return None
    frame = frame.copy()
    if frame.columns.duplicated().any():
        frame = frame.loc[:, ~frame.columns.duplicated()]
    rename_map = {}
    for col in frame.columns:
        text = str(col).strip()
        upper = text.upper()
        canonical = {'OPEN': 'Open', 'HIGH': 'High', 'LOW': 'Low', 'CLOSE': 'Close', 'ADJ CLOSE': 'Adj Close', 'VOLUME': 'Volume'}.get(upper)
        if canonical:
            rename_map[col] = canonical
    if rename_map:
        frame = frame.rename(columns=rename_map)
    if 'Close' not in frame.columns:
        return None
    try:
        if getattr(frame.index, 'tz', None) is not None:
            frame.index = frame.index.tz_localize(None)
    except Exception:
        pass
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep='last')]
    frame = frame[frame['Close'].notna()]
    if frame.empty:
        return None
    if 'Volume' not in frame.columns:
        frame['Volume'] = np.nan
    return frame



def calculate_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50)
    return rsi

def pct_return(close, days):
    if len(close) <= days:
        return np.nan
    return float(close.iloc[-1] / close.iloc[-days - 1] - 1)

def max_drawdown(close):
    peak = close.cummax()
    return float((close / peak - 1).min())

def scaled_points(value, bad_value, good_value, points):
    if pd.isna(value):
        return 0.0
    if value <= bad_value:
        return 0.0
    if value >= good_value:
        return float(points)
    return float((value - bad_value) / (good_value - bad_value) * points)

def rsi_points(rsi):
    if pd.isna(rsi):
        return 0
    if 50 <= rsi <= 65:
        return 12
    if 45 <= rsi < 50:
        return 9
    if 65 < rsi <= 72:
        return 8
    if 40 <= rsi < 45:
        return 6
    if 30 <= rsi < 40:
        return 3
    if 72 < rsi <= 80:
        return 4
    return 0

def volatility_points(vol):
    if vol <= 0.18:
        return 8
    if vol <= 0.28:
        return 6
    if vol <= 0.4:
        return 3
    if vol <= 0.55:
        return 1
    return 0

def drawdown_points(dd):
    if dd >= -0.1:
        return 8
    if dd >= -0.2:
        return 6
    if dd >= -0.3:
        return 3
    if dd >= -0.45:
        return 1
    return 0

def technical_analysis_from_data(data):
    data = _clean_price_frame(data)
    if data is None:
        return None
    close = data['Close'].dropna()
    if len(close) < 63:
        return None
    price = safe_float(close.iloc[-1])
    if price is None:
        return None
    ma20_series = close.rolling(20).mean()
    ma50_series = close.rolling(50).mean()
    ma200_series = close.rolling(200).mean()
    ma20 = safe_float(ma20_series.iloc[-1]) if len(close) >= 20 else None
    ma50 = safe_float(ma50_series.iloc[-1]) if len(close) >= 50 else None
    ma200 = safe_float(ma200_series.iloc[-1]) if len(close) >= 200 else None
    r1 = pct_return(close, 21) if len(close) > 21 else None
    r3 = pct_return(close, 63) if len(close) > 63 else None
    r6 = pct_return(close, 126) if len(close) > 126 else None
    rsi = safe_float(calculate_rsi(close).iloc[-1])
    daily = close.pct_change().dropna()
    annualized_vol = safe_float(daily.std() * np.sqrt(252)) if len(daily) >= 20 else None
    dd = safe_float(max_drawdown(close)) if len(close) >= 20 else None
    score = 0.0
    possible = 0.0

    def add_points(value, bad, good, points):
        nonlocal score, possible
        if value is None:
            return
        score += scaled_points(value, bad, good, points)
        possible += points
    add_points(r1, -0.1, 0.1, 8)
    add_points(r3, -0.2, 0.2, 12)
    add_points(r6, -0.3, 0.3, 16)
    if ma20 is not None:
        possible += 6
        if price > ma20:
            score += 6
    if ma50 is not None:
        possible += 10
        if price > ma50:
            score += 10
    if ma200 is not None:
        possible += 10
        if price > ma200:
            score += 10
    if ma50 is not None and ma200 is not None:
        possible += 10
        if ma50 > ma200:
            score += 10
    if rsi is not None:
        score += rsi_points(rsi)
        possible += 12
    if annualized_vol is not None:
        score += volatility_points(annualized_vol)
        possible += 8
    if dd is not None:
        score += drawdown_points(dd)
        possible += 8
    if possible <= 0:
        return None
    normalized_score = clamp(score / possible * 100.0)
    chart = pd.DataFrame({'Price': close, 'MA 20': ma20_series, 'MA 50': ma50_series, 'MA 200': ma200_series})
    return {'data': data, 'price': price, 'technical_score': normalized_score, 'return_1m': r1, 'return_3m': r3, 'return_6m': r6, 'ma20': ma20, 'ma50': ma50, 'ma200': ma200, 'rsi': rsi if rsi is not None else 50.0, 'annualized_volatility': annualized_vol if annualized_vol is not None else 0.5, 'max_drawdown': dd, 'chart': chart}

def weighted_available(parts):
    if not parts:
        return None
    total_weight = sum((weight for score, weight in parts))
    return clamp(sum((score * weight for score, weight in parts)) / total_weight)

def fundamental_score(fund):
    if not fund:
        return (None, [])
    parts = []
    notes = []
    rev = fund.get('revenue_growth')
    if rev is not None:
        parts.append((clamp((rev + 0.1) / 0.35 * 100), 25))
        notes.append('Revenue growth: {:+.1%}'.format(rev))
    earn = fund.get('earnings_growth')
    if earn is not None:
        parts.append((clamp((earn + 0.2) / 0.55 * 100), 25))
        notes.append('Earnings growth: {:+.1%}'.format(earn))
    margin = fund.get('profit_margin')
    if margin is not None:
        parts.append((clamp((margin - 0.01) / 0.24 * 100), 20))
        notes.append('Profit margin: {:.1%}'.format(margin))
    pe = fund.get('forward_pe')
    if pe is not None:
        if pe <= 0:
            pe_score = 0
        elif 8 <= pe <= 25:
            pe_score = 100
        elif pe < 8:
            pe_score = 65
        elif pe <= 35:
            pe_score = 75
        elif pe <= 50:
            pe_score = 45
        elif pe < 8:
            pe_score = 65
        else:
            pe_score = 20
        parts.append((pe_score, 15))
        notes.append('Forward P/E: {:.1f}'.format(pe))
    debt = fund.get('debt_to_equity')
    if debt is not None and debt >= 0:
        ratio = debt
        if ratio <= 0.3:
            debt_score = 100
        elif ratio <= 0.7:
            debt_score = 75
        elif ratio <= 1.2:
            debt_score = 45
        else:
            debt_score = 20
        parts.append((debt_score, 10))
        notes.append('Debt/equity: {:.2f}'.format(ratio))
    fcf = fund.get('free_cash_flow')
    if fcf is not None:
        parts.append((100 if fcf > 0 else 20, 5))
        notes.append('Free cash flow: {}'.format('positive' if fcf > 0 else 'negative'))
    return (weighted_available(parts), notes)

def looks_like_common_stock_name(name):
    """
    Used only for the Nasdaq Trader fallback directories, which contain
    stocks plus some other listed securities.
    """
    text = str(name or '').upper()
    excluded_terms = [' WARRANT', ' WARRANTS', ' RIGHT', ' RIGHTS', ' UNIT', ' UNITS', ' PREFERRED', ' PREFERENCE', ' ETF', ' ETN', ' EXCHANGE TRADED FUND', ' CLOSED END FUND', ' CLOSED-END FUND', ' BOND', ' NOTES DUE', ' SENIOR NOTE', ' DEBENTURE']
    return not any((term in text for term in excluded_terms))

def _fetch_us_stock_universe():
    """
    Broad U.S.-listed stock universe.

    Primary source:
      Nasdaq's public stock screener, including sector metadata.

    Fallback:
      Nasdaq Trader's Nasdaq-listed and other-exchange-listed
      symbol directories.

    This is called only after Build Portfolio is pressed.
    """
    import requests
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36', 'Accept': 'application/json,text/plain,*/*', 'Accept-Language': 'en-US,en;q=0.9', 'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/market-activity/stocks/screener'}
    try:
        endpoint = 'https://api.nasdaq.com/api/screener/stocks'
        rows_out = []
        seen = set()
        page_size = 5000
        for offset in range(0, 20000, page_size):
            params = {'tableonly': 'true', 'limit': str(page_size), 'offset': str(offset), 'download': 'true'}
            response = requests.get(endpoint, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
            data = payload.get('data') if isinstance(payload, dict) else None
            if not data:
                break
            rows = data.get('rows', []) or []
            if not rows:
                break
            new_count = 0
            for row in rows:
                symbol = normalize_yahoo_symbol(row.get('symbol'))
                if symbol is None or symbol in seen:
                    continue
                seen.add(symbol)
                new_count += 1
                rows_out.append({'Symbol': symbol, 'Company': row.get('name') or symbol, 'Sector': row.get('sector') or 'Unknown', 'Industry': row.get('industry') or '', 'Country': row.get('country') or '', 'Exchange': '', 'Universe Source': 'Nasdaq Stock Screener'})
            if new_count == 0:
                break
            if len(rows) < page_size:
                break
        df = pd.DataFrame(rows_out)
        if len(df) >= 1000:
            return df.drop_duplicates(subset=['Symbol']).sort_values('Symbol').reset_index(drop=True)
    except Exception:
        pass
    try:
        from io import StringIO
        nasdaq_url = 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'
        other_url = 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt'
        basic_headers = {'User-Agent': headers['User-Agent']}
        nasdaq_response = requests.get(nasdaq_url, headers=basic_headers, timeout=20)
        other_response = requests.get(other_url, headers=basic_headers, timeout=20)
        nasdaq_response.raise_for_status()
        other_response.raise_for_status()
        nasdaq_df = pd.read_csv(StringIO(nasdaq_response.text), sep='|', dtype=str)
        other_df = pd.read_csv(StringIO(other_response.text), sep='|', dtype=str)
        rows_out = []
        for _, row in nasdaq_df.iterrows():
            raw_symbol = row.get('Symbol')
            if raw_symbol is None or str(raw_symbol).startswith('File Creation Time'):
                continue
            if str(row.get('Test Issue', 'N')).upper() == 'Y':
                continue
            if str(row.get('ETF', 'N')).upper() == 'Y':
                continue
            name = row.get('Security Name', raw_symbol)
            if not looks_like_common_stock_name(name):
                continue
            symbol = normalize_yahoo_symbol(raw_symbol)
            if symbol is None:
                continue
            rows_out.append({'Symbol': symbol, 'Company': name, 'Sector': 'Unknown', 'Industry': '', 'Country': '', 'Exchange': 'NASDAQ', 'Universe Source': 'Nasdaq Trader Directory'})
        exchange_names = {'A': 'NYSE American', 'N': 'NYSE', 'P': 'NYSE Arca', 'Z': 'Cboe/BATS', 'V': 'IEX'}
        for _, row in other_df.iterrows():
            raw_symbol = row.get('NASDAQ Symbol') or row.get('ACT Symbol')
            if raw_symbol is None or str(raw_symbol).startswith('File Creation Time'):
                continue
            if str(row.get('Test Issue', 'N')).upper() == 'Y':
                continue
            if str(row.get('ETF', 'N')).upper() == 'Y':
                continue
            name = row.get('Security Name', raw_symbol)
            if not looks_like_common_stock_name(name):
                continue
            symbol = normalize_yahoo_symbol(raw_symbol)
            if symbol is None:
                continue
            exchange_code = str(row.get('Exchange', '')).strip()
            rows_out.append({'Symbol': symbol, 'Company': name, 'Sector': 'Unknown', 'Industry': '', 'Country': '', 'Exchange': exchange_names.get(exchange_code, exchange_code), 'Universe Source': 'Nasdaq Trader Directory'})
        df = pd.DataFrame(rows_out)
        if not df.empty:
            return df.drop_duplicates(subset=['Symbol']).sort_values('Symbol').reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame(columns=['Symbol', 'Company', 'Sector', 'Industry', 'Country', 'Exchange', 'Universe Source'])
