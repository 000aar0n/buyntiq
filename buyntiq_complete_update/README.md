# Buyntiq

A monochrome stock research workspace with **four separate pages**:

| Page | Address | Purpose |
| --- | --- | --- |
| Home | `/` | Feature shortcuts, watchlist, and recent session work |
| Stock Research | `/research` | Company analysis, charts, ML validation, and news |
| Portfolio Builder | `/builder` | Candidate screening, final-score ranking, and allocations |
| Portfolio Review | `/review` | Enter/import stocks and shares, then inspect value and risk |

## Start on Windows

1. Extract the ZIP. Open the `buyntiq` folder.
2. Install **Python 3.12** if needed. Python 3.11 also works with the launcher.
3. Double-click **`start_windows.bat`**. The first launch installs dependencies.
4. Open **http://localhost:8501** if the browser does not open automatically.

For a quick walkthrough, switch **Demo data** on at the top. It uses clearly labeled synthetic prices and financials. Switch it off to request real market data. Demo data is never used as a fallback for a failed real request.

## Start with commands (macOS, Linux, or an existing environment)

Run these from the extracted `buyntiq` folder using Python 3.12:

```bash
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Replace the old Streamlit app

This replaces the single-file layout with an application folder. **Copy the entire project**, including `buyntiq/`, `views/`, `assets/`, `requirements.txt`, and `.streamlit/config.toml`. Do not copy just `app.py`.

On Streamlit Community Cloud, set the entrypoint to **`app.py`** (or `buyntiq/app.py` if you put this entire folder inside your repository), use Python 3.12, and reboot after updating dependencies. Run from the project root so its monochrome theme configuration is loaded. Keep any secrets out of the repository. No API key is required by this version.

If the app is nested inside your repository, also copy `.streamlit/config.toml` to the **repository root's** `.streamlit` folder so Community Cloud loads the theme.

## What changed

- Native Streamlit pages and distinct URLs; the three tools no longer run together.
- A consistent charcoal, white, and gray visual system, responsive layouts, and visible keyboard focus.
- Session state preserves results and holdings while navigating. Mode changes clear results so real and demo data cannot mix.
- Market requests begin only after an action. Ten-year prices cache for 6 hours, company data for 12 hours, and content-keyed ML results for 6 hours. News and additional forecast horizons load only when requested.
- Bounded data-request concurrency, provider timeouts, short failure caches, and labeled stale data instead of repeated full retries.
- Ridge, Extra Trees, and Gradient Boosting candidates with separate development, calibration, and holdout periods.
- Research scores in all three tools use the same formula. Forecasts that fail the baseline gate have **zero ML score weight**.
- Highest scores selects the highest **final** research scores among fully analyzed finalists. Diversified selection is explicit and its penalties are documented in the app.
- Fractional holdings, duplicate-symbol merging, CSV import/export, sector weights, concentration, correlations, and shrunk covariance risk estimates.

## How to use the builder

The starter list contains 62 named stocks across 11 sectors. It is a convenient research universe, not a complete market index. Custom symbols support up to 100 names. The live US directory option screens a disclosed sample of up to 500 symbols; it does not claim to analyze every listed stock.

The first pass ranks technical scores. The requested number of finalists receives full company and ML analysis. **Highest scores ranks that finalist pool**, not stocks that were never fully analyzed. Increase the finalist count to broaden the comparison; it takes longer. The risk profile changes weights and caps, while selection mode controls which stocks are chosen.

Target weights are fractional allocations. Whole shares round down, and the balance is shown as cash. Risk metrics describe target weights, not a trade execution simulation.

## Forecast interpretation

The ML output is an estimate of a future holding-period return, not a promised target or probability of profit. The app displays return error versus a simple baseline, direction accuracy, measured interval coverage, and the small number of non-overlapping validation windows. The model brief is generated directly from these numeric results; it does not use an external language model or invent company facts.

See **`MODEL_CARD.md`** for the exact training boundaries, score formula, and limitations. These changes make model behavior more transparent and better controlled; they do not establish real-world predictive superiority or profitable trading performance.

## Data and privacy

- Live data comes from Yahoo Finance through yfinance. The directory comes from Nasdaq endpoints. Provider access and formats can change.
- Quotes use adjusted daily history, not a live execution feed. A cached response has a displayed price date. Fallback data is explicitly marked stale.
- Portfolio calculations accept confirmed USD quotes. A company's financial reporting currency is tracked separately.
- Missing data is not fabricated. Unpriced/unverified positions are excluded and disclosed; coverage by value cannot be known for unpriced holdings.
- Holdings and watchlists stay in the Streamlit session and disappear when that session resets. Download CSVs to keep results. Public market data and model outputs are the only disk-cached content.
- There is no brokerage connection, order execution, scheduled training, persistent account system, or external LLM call.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover causal features, label-boundary gaps, score gating, allocation math, final-score selection, provider failures, currency handling, page isolation, session persistence, and both portfolio workflows. Synthetic-data tests verify behavior, not market prediction accuracy. See `VERIFICATION.md` for results from this build.

## Project layout

- `app.py`: page routing and shared frame
- `views/`: one Python file per page
- `buyntiq/data.py`, `cache.py`: market sources and cache
- `buyntiq/features.py`, `model.py`: causal features and validated ensemble
- `buyntiq/analytics.py`, `portfolio.py`: shared scores and allocations
- `buyntiq/legacy_calculations.py`: retained technical/company calculations and directory parsing
- `buyntiq/ui.py`, `assets/style.css`: reusable monochrome UI
- `tests/`: model, data, and app checks

Set `BUYNTIQ_CACHE_DIR` to choose a persistent public-data cache folder. By default it uses a temporary directory. Set `BUYNTIQ_DEMO=1` before starting to open directly in demo mode. The data cache is disposable; deleting it only causes fresh downloads and retraining.
