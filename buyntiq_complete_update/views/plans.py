"""Checkout return parameters are informational; server-verified membership grants access."""
from datetime import datetime, timezone
import streamlit as st
from buyntiq import accounts, billing, ui

ui.header("04 / Membership", "Your research, your pace.", "Start free. Upgrade for forecasts and portfolio tools.")
settings = billing.config()
identity = accounts.current_identity()
cycles = list(settings.get("options", {"monthly": None, "annual": None}))
cycle = st.radio("Billing period", cycles, format_func=lambda value: value.title(),
                 horizontal=True, key="subscription_cycle")
if settings and not settings["live"]:
    st.warning("TEST MODE — this checkout uses Stripe test payments, not real charges.")
if st.query_params.get("checkout") == "returned":
    st.info("Back from checkout? Sign in with the same account, then select Refresh subscription. A return link alone does not confirm payment.")
left, right = st.columns(2)
with left, st.container(border=True):
    st.subheader("Free")
    st.write("**$0**")
    st.write(f"• {billing.limit_for('research', False)} stock analyses per day\n• Price charts, company data and news\n• Saved watchlist and recent searches")
    st.caption("A free account is required to run analyses.")
with right, st.container(border=True):
    st.subheader("Pro")
    ready = False
    try:
        price = billing.price_details(cycle)
        period = billing.CYCLES[cycle]
        st.write(f"**${price['unit_amount']/100:,.2f} USD / {period}**")
        if cycle == "annual":
            st.caption(f"Billed once per year. Equivalent to ${price['unit_amount']/1200:,.2f} per month.")
        st.caption("Unlimited Pro usage with either billing period.")
        ready = True
    except billing.BillingError as exc:
        st.caption(str(exc))
    st.write("• Unlimited stock analyses with ML forecasts\n• Unlimited portfolio builds and reviews\n• Unlimited additional forecast bundles\n• Forecast and portfolio CSV exports")
    st.caption(f"Renews {'annually' if cycle == 'annual' else 'monthly'} until canceled. Manage cancellation through the billing portal. Estimates are uncertain; Pro does not guarantee better returns.")

if not identity:
    st.info("Open Account above to sign in or create your free account before subscribing.")
    st.stop()

if st.button("Refresh subscription"):
    billing.access(force=True)
    billing.sync_access()
    st.rerun()
access = billing.access()
if not access.verified:
    st.warning(access.message)
elif access.status == "owner":
    st.success("Owner Pro is active — all Pro features, no subscription required.")
    st.caption("No daily application limits apply.")
elif access.pro:
    end = datetime.fromtimestamp(access.expires_at, timezone.utc).strftime("%B %d, %Y")
    st.success(f"Buyntiq Pro is active. Current paid period ends {end} (UTC).")
else:
    st.write("Your current plan: **Free**")

if ready and not access.pro and st.button("Subscribe to Pro", type="primary"):
    try:
        # The link belongs to this account, environment and selected billing period.
        url = billing.start_checkout(cycle)
        st.session_state._checkout_link = (identity.key, settings["live"], price["id"], url)
    except billing.BillingError as exc:
        st.error(str(exc))
link = st.session_state.get("_checkout_link")
if (ready and link and len(link) == 4 and link[:3] == (identity.key, settings["live"], price["id"])
        and not access.pro):
    st.link_button("Continue to secure Stripe checkout", link[3], type="primary")
    st.caption("Keep this app tab open. After payment, return here and select Refresh subscription. A new app tab may require another sign-in code.")
if access.pro and access.status != "owner":
    st.caption("Already subscribed? Use the billing portal below. Choosing a different period here does not change your existing subscription.")
if settings and access.status != "owner" and st.button("Manage subscription / cancel"):
    try:
        st.session_state._portal_link = (identity.key, billing.start_portal())
    except billing.BillingError as exc:
        st.error(str(exc))
link = st.session_state.get("_portal_link")
if link and link[0] == identity.key:
    st.link_button("Open Stripe billing portal", link[1])

if access.pro:
    st.caption("Pro has no daily application limits. Market-data availability and hosting capacity still apply.")
else:
    try:
        used = billing._store().usage(identity.key)
        st.caption("Today's usage (resets at midnight UTC): " + "; ".join(f"{name}: {count}" for name, count in used.items()) if used else "No analyses used today.")
    except Exception:
        st.caption("Usage totals are temporarily unavailable.")
    st.caption("Manual and hourly research refreshes count toward Free usage. Reopening saved results does not. Failed computations normally return the reserved run.")

with st.expander("Account identifier"):
    st.caption("This identifies your current login. The app owner can grant complimentary Pro through Streamlit Secrets.")
    st.code(identity.key, language=None)
