"""Supabase email-code authentication. Tokens live only in this server session.

No shared authenticated client, query-string token, or browser-stored token is
used. Refreshing/closing the browser session requires signing in again.
"""
import hashlib
import time
import uuid
from urllib.parse import urlsplit

import requests
import streamlit as st


class AuthError(ValueError):
    pass


def config():
    try:
        values = dict(st.secrets.get("supabase", {}))
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return {}
    url = str(values.get("url", "")).rstrip("/")
    key = str(values.get("publishable_key", ""))
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.path or parsed.query or parsed.fragment
            or not key or key.startswith(("YOUR_", "PASTE_", "sb_secret_"))):
        return {}
    return {"url": url, "key": key}


def _request(method, path, payload=None, token=None):
    settings = config()
    if not settings:
        raise AuthError("Email sign-in is not configured yet.")
    headers = {"apikey": settings["key"], "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        response = requests.request(method, settings["url"] + "/auth/v1/" + path,
                                    headers=headers, json=payload, timeout=(5, 15),
                                    allow_redirects=False)
    except requests.RequestException:
        raise AuthError("Email sign-in is temporarily unavailable. Please retry.") from None
    if response.status_code == 429:
        raise AuthError("Too many attempts. Please wait before trying again.")
    if not 200 <= response.status_code < 300:
        # Provider bodies can include identity/configuration details. Do not echo them.
        raise AuthError("Sign-in failed. Check the email/code or request a new code. If no email arrives, the app owner must check Supabase email delivery.")
    if not response.content:
        return {}
    try:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError
        return value
    except ValueError:
        raise AuthError("The sign-in service returned an invalid response.") from None


def _verified_user(access_token):
    user = _request("GET", "user", token=access_token)
    try:
        subject = str(uuid.UUID(user["id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AuthError("Could not verify this account.") from None
    if not user.get("email_confirmed_at") or not user.get("email"):
        raise AuthError("Verify your email before signing in.")
    return {"id": subject, "email": str(user["email"])[:320]}


def _session(payload):
    token, refresh = payload.get("access_token"), payload.get("refresh_token")
    if not isinstance(token, str) or not token or not isinstance(refresh, str) or not refresh:
        raise AuthError("Could not establish a verified session.")
    user = _verified_user(token)
    try:
        lifetime = min(int(payload.get("expires_in", 3600)), 86400)
        if lifetime <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise AuthError("The sign-in service returned an invalid session.") from None
    return {"access_token": token, "refresh_token": refresh, "user": user,
            "expires_at": time.time() + lifetime, "checked_at": time.time(),
            "project": config()["url"]}


def identity_claims(force=False):
    session = st.session_state.get("_email_auth")
    if not session:
        return None
    try:
        if session.get("project") != config().get("url"):
            raise AuthError("Account project changed. Sign in again.")
        if session["expires_at"] <= time.time() + 30:
            refreshed = _session(_request("POST", "token?grant_type=refresh_token",
                               {"refresh_token": session["refresh_token"]}))
            if refreshed["user"]["id"] != session["user"]["id"]:
                raise AuthError("Account identity changed. Sign in again.")
            session = refreshed
        elif force or time.time() - session["checked_at"] >= 60:
            user = _verified_user(session["access_token"])
            if user["id"] != session["user"]["id"]:
                raise AuthError("Account identity changed. Sign in again.")
            session = dict(session, user=user, checked_at=time.time())
        st.session_state._email_auth = session
        user = session["user"]
        # Provider + project + stable subject, never a claimed email address.
        key = hashlib.sha256(("supabase:" + session["project"] + ":" + user["id"]).encode()).hexdigest()
        return {"key": key, "name": "Your account", "email": user["email"]}
    except (AuthError, KeyError, TypeError):
        st.session_state.pop("_email_auth", None)
        return None


def send_code(email):
    email = str(email).strip().lower()
    if len(email) > 254 or email.count("@") != 1 or " " in email or "." not in email.split("@")[-1]:
        raise AuthError("Enter a valid email address.")
    if time.time() - st.session_state.get("_email_last_sent", 0) < 60:
        raise AuthError("Wait one minute before requesting another code.")
    # Supabase applies its own project/IP/email rate limits in addition to this UI cooldown.
    st.session_state._email_last_sent = time.time()
    _request("POST", "otp", {"email": email, "create_user": True})
    st.session_state._email_pending = email
    st.session_state._email_verify_attempt = 0.


def verify_code(code):
    email = st.session_state.get("_email_pending")
    code = str(code).strip()
    if not email or not code.isdigit() or not 6 <= len(code) <= 10:
        raise AuthError("Enter the code from your latest email.")
    if time.time() - st.session_state.get("_email_verify_attempt", 0) < 2:
        raise AuthError("Please wait a moment before trying again.")
    st.session_state._email_verify_attempt = time.time()
    session = _session(_request("POST", "verify", {"email": email, "token": code, "type": "email"}))
    st.session_state.clear()
    from buyntiq.state import initialize
    initialize()
    st.session_state._email_auth = session


def logout():
    session = st.session_state.get("_email_auth")
    try:
        if session:
            _request("POST", "logout?scope=local", token=session["access_token"])
    except AuthError:
        pass
    finally:
        st.session_state.clear()


def render():
    """Called inside the existing Account popup."""
    if not config():
        st.caption("Email sign-in is being set up. You can browse as a guest.")
        return
    st.write("Sign in or create your free account")
    with st.form("email_send_form"):
        email = st.text_input("Email address", max_chars=254)
        send = st.form_submit_button("Email me a sign-in code", width="stretch")
    if send:
        try:
            send_code(email)
            st.success("Check your inbox for your sign-in code.")
        except AuthError as exc:
            st.error(str(exc))
    if st.session_state.get("_email_pending"):
        st.caption("Code sent to " + st.session_state._email_pending)
        with st.form("email_verify_form", clear_on_submit=True):
            code = st.text_input("Sign-in code", max_chars=10, type="password")
            verify = st.form_submit_button("Verify and sign in", width="stretch")
        if verify:
            try:
                verify_code(code)
                st.rerun()
            except AuthError as exc:
                st.error(str(exc))
    st.caption("Your saved lists follow your account. Reloading this browser session may require a new sign-in code.")
