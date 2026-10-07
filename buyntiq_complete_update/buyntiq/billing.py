"""Server-enforced Free/Pro plans; Stripe is the subscription source of truth.

No success URL, email address, client-side flag, or demo toggle grants Pro.
A verified account explicitly allowlisted in server secrets receives owner Pro.
Other accounts require a paid, active Stripe subscription. Every paid action
rechecks authorization. Display checks are cached for 30 seconds.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import re
import time
from urllib.parse import urlsplit

import requests
import streamlit as st
from buyntiq import accounts


class BillingError(ValueError):
    pass


@dataclass(frozen=True)
class Access:
    pro: bool = False
    verified: bool = True
    expires_at: float = 0.
    status: str = "free"
    message: str = ""


DEFAULT_LIMITS = {"free_research": 5}
CYCLES = {"monthly": "month", "annual": "year"}


def config():
    values = accounts._settings("stripe")
    key, price = str(values.get("secret_key", "")).strip(), str(values.get("price_id", "")).strip()
    annual = str(values.get("annual_price_id", "")).strip()
    site = str(values.get("site_url", "https://buyntiq.streamlit.app")).rstrip("/")
    parsed = urlsplit(site)
    if (not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_"))
            or not re.fullmatch(r"price_[A-Za-z0-9]+", price) or parsed.scheme != "https"
            or not parsed.hostname or parsed.username or parsed.path or parsed.query or parsed.fragment):
        return {}
    extra = values.get("additional_pro_price_ids", [])
    if not isinstance(extra, list):
        extra = []
    options = {"monthly": price}
    if re.fullmatch(r"price_[A-Za-z0-9]+", annual) and annual != price:
        options["annual"] = annual
    return {"key": key, "price": price, "options": options, "site": site,
            "prices": {*options.values(), *[p for p in extra if isinstance(p, str) and re.fullmatch(r"price_[A-Za-z0-9]+", p)]},
            "live": key.startswith(("sk_live_", "rk_live_"))}


def selected_price(settings, cycle):
    if cycle not in CYCLES:
        raise BillingError("Choose Monthly or Annual billing.")
    price = settings.get("options", {"monthly": settings.get("price")}).get(cycle)
    if not price:
        raise BillingError(f"{cycle.title()} subscriptions are not available yet.")
    return price


def limit_for(feature, pro):
    if pro:
        return None  # Pro has no application daily quota.
    name = ("pro_" if pro else "free_") + feature
    if name not in DEFAULT_LIMITS:
        return 0
    value = accounts._settings("plans").get(name + "_per_day", DEFAULT_LIMITS[name])
    try:
        return max(1, min(int(value), 1000))
    except (TypeError, ValueError):
        return DEFAULT_LIMITS[name]


def _stripe(method, path, data=None, *, idempotency=None):
    settings = config()
    if not settings:
        raise BillingError("Subscriptions are not available yet.")
    headers = {"Authorization": "Bearer " + settings["key"],
               "Stripe-Version": "2025-03-31.basil"}
    if idempotency:
        headers["Idempotency-Key"] = idempotency
    try:
        response = requests.request(method, "https://api.stripe.com/v1/" + path,
                                    headers=headers, params=data if method == "GET" else None,
                                    data=data if method != "GET" else None,
                                    timeout=(5, 20), allow_redirects=False)
        if response.status_code in {401, 403}:
            raise BillingError(
                "Stripe rejected the API key or its permissions. In Streamlit Secrets, use the "
                "Stripe secret key for the same live/test account as your Price IDs."
            )
        if response.status_code == 404 and path.startswith("prices/"):
            raise BillingError("This price was not found in the configured Stripe account. Check that both the key and price IDs come from the same sandbox or live account.")
        if not 200 <= response.status_code < 300:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            error = payload.get("error") if isinstance(payload, dict) else {}
            code = str(error.get("code", "")).strip() if isinstance(error, dict) else ""
            message = str(error.get("message", "")).strip() if isinstance(error, dict) else ""
            if code == "resource_missing":
                raise BillingError(
                    "Stripe could not find one of the configured billing objects. Make sure the "
                    "secret key and both Price IDs are from the same Stripe account and mode."
                )
            if code in {"api_key_expired", "account_invalid"}:
                raise BillingError("The configured Stripe account/key is no longer usable. Update the Stripe secret key in Streamlit Secrets.")
            if path == "checkout/sessions" and message:
                # Stripe's Checkout error messages are safe configuration/payment setup guidance;
                # never include request headers or secret values.
                raise BillingError("Stripe could not create checkout: " + message)
            raise BillingError("Stripe returned an error while preparing billing. Please retry.")
        result = response.json()
        if not isinstance(result, dict):
            raise BillingError("Billing returned an invalid response.")
        return result
    except (requests.RequestException, ValueError) as exc:
        if isinstance(exc, BillingError):
            raise
        raise BillingError("Billing could not be reached. No access change was made.") from None


def _safe_url(value, hostname):
    if not isinstance(value, str):
        raise BillingError("Billing returned an invalid link.")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname != hostname or parsed.username:
        raise BillingError("Billing returned an invalid link.")
    return value


def subscriptions(customer):
    result, after = [], None
    for _ in range(10):
        query = {"customer": customer, "status": "all", "limit": 100,
                 "expand[]": "data.latest_invoice"}
        if after:
            query["starting_after"] = after
        page = _stripe("GET", "subscriptions", query)
        rows = page.get("data")
        if not isinstance(rows, list):
            raise BillingError("Subscription status could not be verified.")
        result.extend(rows)
        if not page.get("has_more"):
            return result
        if not rows:
            break
        after = rows[-1]["id"]
    raise BillingError("Subscription status could not be verified.")


def paid_access(rows, customer, prices, live, now=None):
    """Pure decision function, also covering older subscription period fields."""
    now = time.time() if now is None else now
    ends = []
    for sub in rows:
        if not isinstance(sub, dict) or sub.get("pause_collection"):
            continue
        if sub.get("customer") != customer or sub.get("livemode") is not live or sub.get("status") != "active":
            continue
        invoice = sub.get("latest_invoice")
        if not isinstance(invoice, dict) or invoice.get("status") != "paid":
            continue
        for item in sub.get("items", {}).get("data", []):
            price = item.get("price") or {}
            if price.get("id") not in prices:
                continue
            end = item.get("current_period_end", sub.get("current_period_end", 0))
            if isinstance(end, (int, float)) and end > now:
                ends.append(end)
    if ends:
        return Access(True, True, max(ends), "active")
    return Access()


class BillingStore:
    def __init__(self, connect=None):
        self.connect = connect or (lambda: accounts._store()._connect())

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '5s'")
            connection.execute("SET LOCAL lock_timeout = '3s'")
            yield connection

    def customer(self, owner, live):
        accounts.AccountStore._validate_owner(owner)
        with self.transaction() as connection:
            row = connection.execute("SELECT stripe_customer_id FROM buyntiq_private.billing_customers WHERE owner_key=%s AND livemode=%s", (owner, live)).fetchone()
            return row[0] if row else None

    def ensure_customer(self, identity, live):
        accounts.AccountStore._validate_owner(identity.key)
        with self.transaction() as connection:
            # Serializes creation across tabs/workers without relying on session state.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (identity.key + str(live),))
            row = connection.execute("SELECT stripe_customer_id FROM buyntiq_private.billing_customers WHERE owner_key=%s AND livemode=%s", (identity.key, live)).fetchone()
            if row:
                return row[0]
            customer = _stripe("POST", "customers", {"metadata[buyntiq_owner]": identity.key},
                               idempotency="buyntiq-customer-" + identity.key + "-" + str(live))
            if not str(customer.get("id", "")).startswith("cus_") or customer.get("livemode") is not live:
                raise BillingError("Billing account could not be verified.")
            connection.execute("INSERT INTO buyntiq_private.billing_customers(owner_key,livemode,stripe_customer_id) VALUES (%s,%s,%s)", (identity.key, live, customer["id"]))
            return customer["id"]

    def checkout(self, identity, settings, cycle="monthly"):
        price_id = selected_price(settings, cycle)
        customer = self.ensure_customer(identity, settings["live"])
        with self.transaction() as connection:
            row = connection.execute("SELECT checkout_session_id FROM buyntiq_private.billing_customers WHERE owner_key=%s AND livemode=%s FOR UPDATE", (identity.key, settings["live"])).fetchone()
            if not row:
                raise BillingError("Billing account could not be verified.")
            if any(s.get("status") not in {"canceled", "incomplete_expired"} for s in subscriptions(customer)):
                raise BillingError("You already have a subscription or pending payment. Use Manage subscription to resolve it.")
            previous = row[0]
            if previous:
                session = _stripe("GET", "checkout/sessions/" + previous)
                if session.get("status") == "open" and session.get("expires_at", 0) > time.time():
                    items = _stripe("GET", "checkout/sessions/" + previous + "/line_items")
                    if any(i.get("price", {}).get("id") == price_id for i in items.get("data", [])):
                        return _safe_url(session.get("url"), "checkout.stripe.com")
                    _stripe("POST", "checkout/sessions/" + previous + "/expire")
            # Stable for retries of the same checkout payload, but versioned so
            # deliberate payload changes never collide with Stripe's stored
            # idempotency record from an older Buyntiq release.
            checkout_request_version = "standard-checkout-v2"
            key = hashlib.sha256(
                (checkout_request_version + "|" + customer + "|" + price_id + "|" + (previous or "first")).encode()
            ).hexdigest()
            payload = {"mode": "subscription", "customer": customer,
                       "client_reference_id": identity.key,
                       "line_items[0][price]": price_id, "line_items[0][quantity]": 1,
                       # Buyntiq uses standard Stripe Checkout. Stripe accounts can
                       # default new sessions to Managed Payments, which requires
                       # additional product tax-code setup and rejects normal
                       # Checkout configuration. Opt out explicitly per session.
                       "managed_payments[enabled]": "false",
                       "subscription_data[metadata][buyntiq_owner]": identity.key,
                       "success_url": settings["site"] + "/plans?checkout=returned",
                       "cancel_url": settings["site"] + "/plans?checkout=canceled"}
            session = _stripe("POST", "checkout/sessions", payload, idempotency="buyntiq-checkout-" + key)
            url = _safe_url(session.get("url"), "checkout.stripe.com")
            connection.execute("UPDATE buyntiq_private.billing_customers SET checkout_session_id=%s WHERE owner_key=%s AND livemode=%s", (session["id"], identity.key, settings["live"]))
            return url

    def reserve(self, owner, feature, limit):
        accounts.AccountStore._validate_owner(owner)
        with self.transaction() as connection:
            row = connection.execute("""INSERT INTO buyntiq_private.usage_daily(owner_key,usage_day,feature,used)
                VALUES (%s,(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date,%s,1)
                ON CONFLICT(owner_key,usage_day,feature) DO UPDATE
                SET used=buyntiq_private.usage_daily.used+1
                WHERE buyntiq_private.usage_daily.used < %s
                RETURNING usage_day,used""", (owner, feature, limit)).fetchone()
            if row is None:
                raise BillingError(f"Your daily limit of {limit} {feature} runs has been reached. Limits reset at midnight UTC.")
            return row[0]

    def refund(self, owner, feature, day):
        accounts.AccountStore._validate_owner(owner)
        with self.transaction() as connection:
            connection.execute("UPDATE buyntiq_private.usage_daily SET used=GREATEST(0,used-1) WHERE owner_key=%s AND usage_day=%s AND feature=%s", (owner, day, feature))

    def usage(self, owner):
        accounts.AccountStore._validate_owner(owner)
        with self.transaction() as connection:
            return dict(connection.execute("SELECT feature,used FROM buyntiq_private.usage_daily WHERE owner_key=%s AND usage_day=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date", (owner,)).fetchall())


def _store():
    return BillingStore()


def _identity(force=False):
    if force and st.session_state.get("_email_auth"):
        from buyntiq import email_auth
        email_auth.identity_claims(force=True)
    return accounts.current_identity()


def owner_access(identity):
    """Only immutable, authenticated account keys in private server settings qualify."""
    keys = accounts._settings("plans").get("owner_account_keys", [])
    return bool(identity and isinstance(keys, list)
                and re.fullmatch(r"[0-9a-f]{64}", identity.key)
                and identity.key in keys)


def access(force=False):
    identity = _identity(force)
    settings = config()
    if owner_access(identity):
        result = Access(pro=True, status="owner")
    elif not identity or not settings:
        result = Access()
    else:
        fingerprint = hashlib.sha256((settings["key"] + ",".join(sorted(settings["prices"]))).encode()).hexdigest()
        cache_key = (identity.key, fingerprint)
        saved = st.session_state.get("_billing_checked")
        if (not force and saved and saved[0] == cache_key and time.time() - saved[1] < 30
                and (not saved[2].pro or saved[2].expires_at > time.time())):
            return saved[2]
        try:
            customer = _store().customer(identity.key, settings["live"])
            result = paid_access(subscriptions(customer), customer, settings["prices"], settings["live"]) if customer else Access()
        except Exception:
            result = Access(False, False, message="We couldn't verify your subscription. Please retry before using Pro features.")
        st.session_state._billing_checked = (cache_key, time.time(), result)
    _record_access(result)
    return result


def _record_access(result):
    previous = st.session_state.get("_billing_was_pro")
    if previous is not None and previous != result.pro:
        # Never show cached ML/portfolio results after an account loses access.
        from buyntiq.state import change_mode
        change_mode()
    st.session_state._billing_was_pro = result.pro


def sync_access():
    result = access()
    _record_access(result)
    return result


def paywall(feature="This feature"):
    st.info("🔒 " + feature + " is included with Buyntiq Pro.")
    benefits = {
        "Portfolio builder": "Screen stocks, choose your investment horizon, and create an allocation with projected portfolio returns.",
        "Portfolio review": "Review your holdings, portfolio forecasts, sector exposure and historical risk. Export your results as CSV.",
        "ML forecasts": "Explore 1-month, 3-month, 6-month and 1-year estimates, uncertainty ranges and model validation results.",
    }
    if feature in benefits:
        st.write(benefits[feature])
    st.page_link("views/plans.py", label="View Pro plans — Monthly or Annual", icon="🔓", width="stretch")
    if not accounts.current_identity():
        st.caption("Open Account above to sign in or create a free account.")


def require_pro(feature):
    result = access()
    if not result.verified:
        st.warning(result.message)
    if not result.pro:
        paywall(feature)
        st.stop()


@contextmanager
def action(feature):
    """Authorizes and atomically reserves quota before any computation."""
    if feature not in {"research", "build", "review", "forecasts"}:
        raise BillingError("Unknown feature.")
    identity = _identity(force=True)
    if not identity:
        raise BillingError("Open Account to sign in before running an analysis.")
    result = access(force=True)
    current = accounts.current_identity()
    if not current or current.key != identity.key:
        raise BillingError("Your sign-in expired. Open Account and sign in again.")
    if not result.verified:
        raise BillingError(result.message)
    if feature != "research" and not result.pro:
        raise BillingError("This feature requires an active Buyntiq Pro subscription.")
    if result.pro:
        yield result
        return
    try:
        day = _store().reserve(identity.key, feature, limit_for(feature, False))
    except BillingError:
        raise
    except Exception:
        raise BillingError("Usage limits could not be checked. Please retry or contact the app owner.") from None
    try:
        yield result
    except BaseException:
        # Failed provider computations do not normally consume a run.
        try:
            _store().refund(identity.key, feature, day)
        except Exception:
            pass
        raise


def price_details(cycle="monthly"):
    settings = config()
    if not settings:
        raise BillingError("Subscriptions are being set up. Free accounts are available.")
    price_id = selected_price(settings, cycle)
    price = _stripe("GET", "prices/" + price_id)
    recurring = price.get("recurring") or {}
    if (price.get("id") != price_id or not price.get("active") or price.get("livemode") is not settings["live"]
            or recurring.get("interval") != CYCLES[cycle] or recurring.get("interval_count") != 1
            or recurring.get("usage_type") != "licensed" or price.get("billing_scheme") != "per_unit"
            or price.get("transform_quantity")
            or not isinstance(price.get("unit_amount"), int) or price["unit_amount"] <= 0
            or price.get("currency") != "usd"):
        raise BillingError(f"The app owner must configure an active {cycle} flat-rate USD price before checkout.")
    return price


def start_checkout(cycle="monthly"):
    identity = _identity(force=True)
    if not identity:
        raise BillingError("Sign in with Account before subscribing.")
    settings = config()
    price_details(cycle)  # Authoritative price, interval and test/live validation.
    try:
        # Checkout only needs the billing customer mapping. Free-tier usage counters
        # must never block a customer from reaching Stripe.
        return _store().checkout(identity, settings, cycle)
    except BillingError:
        raise
    except Exception as exc:
        detail = str(exc).lower()
        if ("billing_customers" in detail and
                ("does not exist" in detail or "undefinedtable" in detail or "relation" in detail)):
            raise BillingError(
                "The Buyntiq billing database is not initialized. Run billing_schema.sql in your "
                "Supabase SQL Editor, then retry checkout."
            ) from None
        if "permission denied" in detail or "insufficient privilege" in detail:
            raise BillingError(
                "Buyntiq can reach the billing database but does not have permission to use it. "
                "Check the Supabase database connection in [accounts]."
            ) from None
        if "timeout" in detail or "timed out" in detail:
            raise BillingError("The billing database timed out. Please retry checkout.") from None
        raise BillingError(
            "Checkout reached Stripe setup but the billing account could not be prepared. "
            "Check that billing_schema.sql has been run and that [accounts] database_url is working."
        ) from None


def start_portal():
    identity, settings = _identity(force=True), config()
    if not identity or not settings:
        raise BillingError("Sign in before managing your subscription.")
    try:
        customer = _store().customer(identity.key, settings["live"])
        if not customer:
            raise BillingError("No billing account is linked to this login yet.")
        session = _stripe("POST", "billing_portal/sessions", {"customer": customer,
                          "return_url": settings["site"] + "/plans"})
        return _safe_url(session.get("url"), "billing.stripe.com")
    except BillingError:
        raise
    except Exception:
        raise BillingError("The billing portal could not be opened. Please retry.") from None
