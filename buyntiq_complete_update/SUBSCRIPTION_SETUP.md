# Buyntiq Free + Pro — monthly and annual setup

Buyntiq has a Free tier and one Pro tier with monthly or annual billing. Stripe supplies the actual price. The two payment options grant the same features with no daily application limits.

## 1. Prepare the database before uploading the code

Open your existing project at https://supabase.com/dashboard → SQL Editor → New query.

1. If you have not already run `account_schema.sql`, paste its contents and click Run.
2. In a new query, paste all of `billing_schema.sql` and click Run.

These scripts can be run again without deleting your saved lists. Keep `buyntiq_private` out of the exposed Data API schemas. The existing private `[accounts] database_url` is still required, including for Free usage limits.

## 2. Enable email-code login (no Google Cloud project needed)

In the same Supabase project:

1. Authentication → Sign In / Providers → Email: enable Email and allow new signups. Keep email verification enabled.
2. Authentication → URL Configuration: set Site URL to `https://buyntiq.streamlit.app`.
3. Authentication → Email Templates → Magic Link: use this body and save:

```html
<h2>Your Buyntiq sign-in code</h2>
<p>Enter this code in the Buyntiq Account menu:</p>
<p><strong>{{ .Token }}</strong></p>
<p>If you did not request this, ignore this email.</p>
```

4. Authentication → Email / SMTP Settings: connect your email provider's SMTP host, port, username, password and verified sender address. Configure sender-domain verification at that provider. Supabase's default mail service is only for project-team recipients and is not suitable for public signups. Test with an address outside the project team.
5. Copy the project URL and **publishable key** from the project's Connect/API Keys settings. A legacy **anon** key also works. Do not use a secret or service-role key for this setting.

Users enter the emailed code in the app. Tokens remain in that browser's server session; a full reload, new tab, or app restart may require another code. Watchlists and subscription ownership persist in the database. Existing Google login remains available if configured. Google and email-code logins are separate account identities, even with the same email: subscribe and return using the same sign-in method. This patch does not merge existing accounts.

Supabase documentation:
- https://supabase.com/docs/guides/auth/auth-email-passwordless
- https://supabase.com/docs/guides/auth/auth-smtp

## 3. Stripe sandbox prices

Open https://dashboard.stripe.com and select a testing environment (Sandbox/Test mode).

1. Product catalog → Add product: name it **Buyntiq Pro**.
2. Add two recurring prices: **monthly** and **yearly**, both **USD**, flat amount, quantity 1. Enter the total annual amount for the yearly option. Do not configure a trial or metered pricing.
3. Your supplied sandbox prices are monthly `price_1UNbQDGnhPJ6YaUQwD9TSsoM` and annual `price_1UNbRHGnhPJ6YaUQIhk0cHY9`. Use both with the secret key from that same sandbox. A product ID (`prod_...`) will not work.
4. Developers / API keys: copy the **test secret key** (`sk_test_...`). Keep it private.
5. Settings → Billing → Customer portal: activate/configure the portal for that same testing environment. Allow payment-method updates, invoice access and cancellation **at the end of the billing period**. Leave plan switching off; this app has one paid tier.

The app uses Stripe Checkout and the billing portal. No webhook endpoint or extra server is required: each paid calculation verifies its customer's subscriptions directly with Stripe. Display status may be cached for 30 seconds; Refresh subscription forces a new check. Payment-return query parameters cannot grant access.

## 4. Add Streamlit secrets

Open your app → Manage app → Settings → Secrets. Preserve your existing settings. Add these sections once; edit an existing section instead of duplicating it. Replace placeholder values inside the quotes. Keep section brackets and quotes.

```toml
[accounts]
database_url = "YOUR_EXISTING_PRIVATE_SUPABASE_DATABASE_CONNECTION_STRING"

[supabase]
url = "https://YOUR_PROJECT_REF.supabase.co"
publishable_key = "YOUR_SUPABASE_PUBLISHABLE_OR_ANON_KEY"

[stripe]
secret_key = "YOUR_STRIPE_TEST_SECRET_KEY"
price_id = "price_1UNbQDGnhPJ6YaUQwD9TSsoM"
annual_price_id = "price_1UNbRHGnhPJ6YaUQIhk0cHY9"
site_url = "https://buyntiq.streamlit.app"
```

If `[accounts]` already works, keep its existing value. Do not replace it with the placeholder above. Do not upload real secrets to GitHub or send them in chat. Existing `[auth]` Google settings can remain.

Optional quota overrides (omit to use these defaults):

```toml
[plans]
free_research_per_day = 5
```

Free quotas are per verified account, reset at midnight UTC, and are stored atomically in Postgres. Manual and hourly analysis refreshes count. Viewing saved results does not. Failed computations normally refund the reserved run; a server crash can leave that reservation consumed. Demo mode has the same paywall and quotas. Free users get technical/company scoring without ML; Pro retains the existing ML-enabled scoring. These are product limits, not changes to model training or data providers.

## 5. Repository and deployment

Use branch **master**, with Streamlit entry point **buyntiq_complete_update/app.py**. Keep this existing layout. When the code update is committed directly to GitHub, no manual upload is needed; Streamlit should pick up the commit. Reboot from Manage app if the app stays on the old version.

Run both SQL scripts before using accounts/checkout. Existing secrets can stay: the monthly key remains `price_id`, and the annual key is `annual_price_id`. Missing annual configuration leaves monthly checkout available. Selecting Annual validates that its Stripe Price really recurs once per year. Changing the selector does not change an already active subscription; use Manage subscription, with portal plan switching disabled for this release.

## 6. Test before accepting money

1. Reboot the Streamlit app after secrets and files are saved.
2. Account → enter your email → request code → verify it.
3. Save a watchlist. Log out and back in using the same method; confirm it returns.
4. As Free, run a basic analysis. Confirm ML, builder and review are locked. Free requires the database migration even if Stripe is not configured yet.
5. Plans must show **TEST MODE**, Monthly/Annual choices, the matching amount and the Subscribe button.
6. Click Subscribe → Continue to secure Stripe checkout. Use Stripe's test card `4242 4242 4242 4242`, any future expiry and any three-digit CVC **only in test mode**.
7. Keep the original app tab open. After checkout, return to it and click Refresh subscription. Verify Pro and run a small portfolio calculation.
8. Log in as a different user; confirm that account stays Free and does not see your saved lists/results.
9. Manage subscription / cancel → billing portal. Cancel at period end: Pro should remain through the paid period. In the Stripe test dashboard, cancel immediately to verify access is removed. Failed/past-due payments must not grant Pro. A Stripe/network outage blocks new paid work until verification succeeds.
10. Retry Subscribe twice before paying; it should reuse the pending Checkout session. Switch to Annual before paying: the old monthly link should disappear, and preparing annual checkout expires the old open session. Test annual with a different test account; both periods must unlock Pro. After subscribing, a second subscription must be blocked.

Refunds alone do not cancel a Stripe subscription. If you intend to revoke access after a refund, cancel the subscription too. Paused collection and trialing subscriptions do not grant Pro. No tax calculation, discount codes, trial or proration-based plan changes are configured by this patch.

## 7. Switch to real billing

Only after the test flow passes: activate your live Stripe account, create live monthly and annual product prices, configure the live customer portal, and replace the test secret key and both test price IDs with their **live** equivalents. Test payments never become live subscriptions. Confirm the TEST MODE banner disappears and the correct price appears.

This implementation does not itself provision Stripe, SMTP, Supabase or live billing. Real email delivery, hosted database access and a full Stripe test purchase must be verified in your deployment.

If you later change price IDs, include old paid prices in `[stripe] additional_pro_price_ids = ["price_OLD"]` so existing subscribers keep access. Keep the original Stripe account and database customer mappings. Do not change identity-key logic after taking payments without migrating those mappings.

## Files replaced / added

Replaced: `app.py`, `buyntiq/accounts.py`, `views/research.py`, `views/builder.py`, `views/review.py`, `ACCOUNT_SETUP.md`, `README.md`, `secrets.example.toml`, and existing test fixtures.

Added: `buyntiq/billing.py`, `buyntiq/email_auth.py`, `views/plans.py`, `billing_schema.sql`, `SUBSCRIPTION_SETUP.md`, `SUBSCRIPTION_TESTING.md`, `tests/test_accounts.py`, `tests/test_billing.py`, and a root `.gitignore`.

Included for convenience: existing `account_schema.sql`. No new Python dependency is required beyond the repository's existing requirements.


## Complimentary owner Pro

Sign in to Buyntiq with your own account. Open Plans > Account identifier and copy
the full identifier. In Streamlit Settings > Secrets, add the following inside
your existing `[plans]` section (do not create a second section):

```toml
owner_account_keys = ["PASTE_YOUR_ACCOUNT_IDENTIFIER"]
```

Save and reload. This verified account receives all Pro features without Stripe
payment, with no daily application limits. The database is still required for saved lists and Free users.
Other accounts remain subject to subscription checks. The identifier is tied to
the login provider and immutable user ID; using a different login provider produces
a different identifier. Remove the entry to revoke complimentary access. Never
put this allowlist in user-editable profile metadata or accept it from URL parameters.

Pro bypasses daily usage reservations entirely, including for owner accounts. Legacy `pro_*_per_day` settings are ignored. Provider throttling and hosting capacity can still affect availability.
