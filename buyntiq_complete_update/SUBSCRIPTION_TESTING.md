# Free/Pro verification — 2026-10-06

Based on repository master `c025838`. The monthly and annual Price IDs supplied by the owner are in the example settings; no private credentials are committed.

- `python -m pytest -q`: **101 passed** across the full suite.
- Compilation and `git diff --check` passed.
- Streamlit AppTest covered all five pages, Free and Pro navigation, ML exclusion for Free, research results across navigation, portfolio build/review and exports, email-code login/logout, saved lists, and account-switch cleanup.
- Both billing periods unlock identical Pro access. Server-side price validation rejects wrong intervals, environment, currency, tiered pricing, inactive prices and unknown IDs.
- The Plans selector sends the selected period to checkout. Switching to Annual hides a saved monthly link. The billing service expires the previous open session before creating the replacement; pending same-price checkout is reused and existing subscriptions block duplicates.
- Payment checks deny expired, trialing, past-due, canceled, paused-collection, wrong-customer, wrong-price and wrong-environment subscriptions. Cancel-at-period-end subscriptions retain the paid period. Verification errors block premium work.
- Private account quotas reserve atomically and return a reservation on computation failure. A changed/expired identity cannot reserve on another account. Demo mode uses the same gates.
- Local PGlite/PostgreSQL checks passed for repeated schema creation, quota caps/refunds, distinct customers and test/live mappings, and denial of client-role access to billing tables. PGlite serializes its local requests; hosted multi-process contention was not exercised.

Old test fixtures were updated to explicitly request demo data, use the existing Shares column, account for the existing minimum finalist pool, and authenticate the test's Pro user. The production forecasting models and data-source safeguards were not changed to make tests pass.

Stripe/Supabase Auth calls in automated tests use mocks. Hosted database access, real SMTP delivery, and an end-to-end sandbox purchase/renewal still require verification on the deployed app. No payment was charged by these tests. The browser visual check was not completed (Chromium crashed before rendering); page behavior was tested with Streamlit AppTest.

See SUBSCRIPTION_SETUP.md for deployment and sandbox verification steps.
