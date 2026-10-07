"""Checkout return parameters are informational; server-verified membership grants access."""
from datetime import datetime, timezone
import streamlit as st
from buyntiq import accounts, billing, ui

ui.header(
    "04 / Membership",
    "Buyntiq Pro",
    "Choose a plan, open Stripe's secure checkout, and come straight back to Buyntiq.",
)

settings = billing.config()
identity = accounts.current_identity()

# Keep the Monthly / Annual segmented control readable in Buyntiq's dark UI.
st.markdown(
    """
    <style>
    .st-key-subscription_cycle button {
        background: #141414 !important;
        color: #f5f5f5 !important;
        border-color: #4a4a4a !important;
    }

    .st-key-subscription_cycle button p,
    .st-key-subscription_cycle button span {
        color: inherit !important;
    }

    .st-key-subscription_cycle button[aria-pressed="true"] {
        background: #ffffff !important;
        color: #000000 !important;
        border-color: #ffffff !important;
        font-weight: 800 !important;
    }

    .st-key-subscription_cycle button:hover {
        border-color: #bdbdbd !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if not settings:
    st.error("Billing is not configured yet. Check the [stripe] section in Streamlit Secrets.")
    st.stop()

cycles = list(settings.get("options", {"monthly": None}))
cycle = st.segmented_control(
    "Billing period",
    cycles,
    default=cycles[0],
    format_func=lambda value: "Monthly" if value == "monthly" else "Annual",
    key="subscription_cycle",
)
cycle = cycle or cycles[0]

if settings["live"]:
    st.success("LIVE BILLING — Stripe Checkout is accepting real payments.")
else:
    st.warning("TEST MODE — Stripe will only accept test payments. No real charge will be made.")

returned = st.query_params.get("checkout")
if returned == "returned":
    st.success("Payment flow returned to Buyntiq. Refresh your subscription below to confirm Pro.")
elif returned == "canceled":
    st.info("Checkout was canceled. Nothing was charged.")

try:
    price = billing.price_details(cycle)
    ready = True
except billing.BillingError as exc:
    price = None
    ready = False
    st.error(str(exc))

free_col, pro_col = st.columns(2)

with free_col:
    with st.container(border=True):
        st.subheader("Free")
        st.markdown("### $0")
        st.write(
            f"• {billing.limit_for('research', False)} stock analyses per day\n"
            "• Price charts, company data and news\n"
            "• Saved watchlist and recent searches"
        )

with pro_col:
    with st.container(border=True):
        st.subheader("Pro")
        if price:
            period = billing.CYCLES[cycle]
            st.markdown(f"### ${price['unit_amount']/100:,.2f} / {period}")
            if cycle == "annual":
                st.caption(f"${price['unit_amount']/1200:,.2f}/month equivalent, billed yearly")
        else:
            st.markdown("### Unavailable")
        st.write(
            "• Unlimited stock analyses with ML forecasts\n"
            "• Unlimited portfolio builds and reviews\n"
            "• Forecast and portfolio CSV exports"
        )

if not identity:
    st.info("Sign in from **Account** above, then come back here to subscribe.")
    st.stop()

if st.button("Refresh subscription", width="stretch"):
    billing.access(force=True)
    billing.sync_access()
    st.rerun()

access = billing.access()

if not access.verified:
    st.warning(access.message)
elif access.status == "owner":
    st.success("Owner Pro is active — all Pro features are unlocked.")
elif access.pro:
    end = datetime.fromtimestamp(access.expires_at, timezone.utc).strftime("%B %d, %Y")
    st.success(f"Buyntiq Pro is active through {end} (UTC).")
else:
    st.caption("Current plan: Free")

# Drop stale links when the user, Stripe mode, or selected price changes.
link = st.session_state.get("_checkout_link")
expected = (identity.key, settings["live"], price["id"]) if ready else None
if link and (len(link) != 4 or link[:3] != expected):
    st.session_state.pop("_checkout_link", None)
    link = None

if ready and not access.pro:
    st.divider()
    st.subheader("Upgrade to Pro")
    st.caption("Buyntiq creates a private Stripe Checkout session for this account. Card details never pass through Buyntiq.")

    if st.button("Prepare secure checkout", type="primary", width="stretch"):
        try:
            with st.spinner("Creating your secure Stripe checkout…"):
                url = billing.start_checkout(cycle)
            st.session_state._checkout_link = (
                identity.key,
                settings["live"],
                price["id"],
                url,
            )
            st.rerun()
        except billing.BillingError as exc:
            st.error(str(exc))

    link = st.session_state.get("_checkout_link")
    if link and len(link) == 4 and link[:3] == expected:
        st.success("Checkout is ready.")
        st.link_button(
            "Open secure Stripe checkout ↗",
            link[3],
            type="primary",
            width="stretch",
        )
        st.caption("After payment, Stripe returns you here. Click Refresh subscription to unlock Pro.")

if access.pro and access.status != "owner":
    st.divider()
    st.subheader("Subscription")
    st.caption("Update payment details or cancel your subscription in Stripe's billing portal.")

if settings and access.status != "owner" and st.button(
    "Manage subscription / cancel",
    width="stretch",
    disabled=not access.pro,
):
    try:
        st.session_state._portal_link = (identity.key, billing.start_portal())
    except billing.BillingError as exc:
        st.error(str(exc))

portal = st.session_state.get("_portal_link")
if portal and portal[0] == identity.key:
    st.link_button("Open Stripe billing portal ↗", portal[1], width="stretch")

if access.pro:
    st.caption("Pro has no Buyntiq daily application limits. Market-data availability and hosting capacity still apply.")
else:
    try:
        used = billing._store().usage(identity.key)
        if used:
            st.caption(
                "Today's Free usage (resets at midnight UTC): "
                + "; ".join(f"{name}: {count}" for name, count in used.items())
            )
        else:
            st.caption("No Free analyses used today.")
    except Exception:
        st.caption("Free-usage totals are temporarily unavailable. This does not block Stripe checkout.")

with st.expander("Account identifier"):
    st.caption("Use this only for the private owner Pro allowlist in Streamlit Secrets.")
    st.code(identity.key, language=None)
