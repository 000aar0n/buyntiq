# Buyntiq

A monochrome stock research workspace with **four separate pages**:

| Page | Address | Purpose |
| --- | --- | --- |
| Home | `/` | Feature shortcuts, saved lists, and recent session work |
| Stock Research | `/research` | Company analysis, charts, ML validation, and news |
| Portfolio Builder | `/builder` | Candidate screening, final-score ranking, and allocations |
| Portfolio Review | `/review` | Enter/import stocks and shares, then inspect value and risk |

## Start on Windows

1. Open the app folder containing `app.py` (`buyntiq_complete_update` in this repository).
2. Install **Python 3.12** if needed. Python 3.11 also works with the launcher.
3. Double-click **`start_windows.bat`**. The first launch installs dependencies.
4. Open **http://localhost:8501** if the browser does not open automatically.

For a quick walkthrough, switch **Demo data** on at the top. It uses clearly labeled synthetic prices and financials. Switch it off to request real market data. Demo data is never used as a fallback for a failed real request.

## Start with commands (macOS, Linux, or an existing environment)

Run these from the app folder containing `app.py` using Python 3.12:

```bash
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Deploy or update the Streamlit app

On Streamlit Community Cloud, use branch **`master`** and entry point **`buyntiq_complete_update/app.py`** for this repository. Use Python 3.12 and reboot after dependency updates if needed. For the account update, follow the exact file map in **[ACCOUNT_SETUP.md](ACCOUNT_SETUP.md)**; upload into existing folders and keep the rest of the repository in place.

Google login requires credentials in Streamlit Secrets. Saved account lists require the PostgreSQL table in `account_schema.sql` and its private database connection. **[ACCOUNT_SETUP.md](ACCOUNT_SETUP.md)** explains the Google and Supabase setup. Guest browsing remains available before configuring accounts. Yahoo requests use yfinance without an API key.

If the app is nested inside your repository, also copy `.streamlit/config.toml` to the **repository root's** `.streamlit` folder so Community Cloud loads the theme.

## What changed

- Native Streamlit pages and distinct URLs; the three tools no longer run together.
- A consistent charcoal, white, and gray visual system, responsive layouts, and visible keyboard focus.
- Session state preserves results and holdings while navigating. Mode changes clear results so real and demo data cannot mix.
- Market requests begin only after an action. Ten-year daily prices and company data cache for one hour; content-keyed ML results cache for six hours. News/quote polling runs on the open research page, and additional forecast horizons load on request.
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
- Signed-in watchlists and recent searches persist in a private database under the verified Google account key. Guest lists and portfolio holdings/results stay in the current session. Download CSVs to retain portfolio results. Public market data and model outputs are the only disk-cached content.
- There is no brokerage connection, order execution, scheduled training, or external LLM call.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover causal features, label-boundary gaps, score gating, allocation math, final-score selection, provider failures, currency handling, page isolation, session persistence, and both portfolio workflows. Synthetic-data tests verify behavior, not market prediction accuracy. See `VERIFICATION.md` for results from this build.

## Project layout

- `app.py`: page routing and shared frame
- `buyntiq/accounts.py`, `account_schema.sql`: Google identity and private saved lists
- `views/`: one Python file per page
- `buyntiq/data.py`, `cache.py`: market sources and cache
- `buyntiq/features.py`, `model.py`: causal features and validated ensemble
- `buyntiq/analytics.py`, `portfolio.py`: shared scores and allocations
- `buyntiq/legacy_calculations.py`: retained technical/company calculations and directory parsing
- `buyntiq/ui.py`, `assets/style.css`: reusable monochrome UI
- `tests/`: model, data, and app checks

Set `BUYNTIQ_CACHE_DIR` to choose a persistent public-data cache folder. By default it uses a temporary directory. New sessions use Yahoo data; synthetic examples require explicitly selecting the Demo data toggle. The old `BUYNTIQ_DEMO` environment setting no longer enables it. The public-data cache is disposable and separate from saved account lists.
