"""Google identity plus private, per-account watchlists and recent searches.

Streamlit/Authlib performs OIDC validation. Only st.user supplies the owner.
Credentials stay in Streamlit secrets; no passwords or OAuth tokens are stored.
"""
import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass
import streamlit as st
from buyntiq.data import normalize_symbol, parse_symbols

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMD", "META"]
MAX_WATCHLIST = 30
MAX_RECENT = 12
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


@dataclass(frozen=True)
class Identity:
    key: str
    name: str
    email: str


def google_identity(user):
    """Accept only identity claims that Streamlit has authenticated."""
    if not user.get("is_logged_in", False):
        return None
    subject, issuer = user.get("sub"), user.get("iss")
    if issuer not in GOOGLE_ISSUERS or not isinstance(subject, str) or not 1 <= len(subject) <= 255:
        return None
    key = hashlib.sha256(("google:" + subject).encode()).hexdigest()
    return Identity(key, str(user.get("name") or "Your account")[:100], str(user.get("email") or "")[:320])


def current_identity():
    return google_identity(dict(st.user))


def _settings(section):
    try:
        return dict(st.secrets.get(section, {}))
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return {}


def _google_login_settings():
    """Support Streamlit's default [auth] and named [auth.google] layouts."""
    auth = _settings("auth")
    google = auth.get("google")
    return (auth, google, "google") if isinstance(google, Mapping) else (auth, auth, None)


def login_setup_issues():
    """Return incomplete setting names only; never expose secret values."""
    auth, provider, _ = _google_login_settings()
    values = {**{key: auth.get(key) for key in ("redirect_uri", "cookie_secret")},
              **{key: provider.get(key) for key in ("client_id", "client_secret")}}
    issues = []
    for key, value in values.items():
        if (not isinstance(value, str) or not value.strip()
                or value.strip().startswith(("YOUR_", "REPLACE_", "PASTE_"))
                or (key == "redirect_uri" and "YOUR-APP" in value)):
            issues.append(key)
    if provider.get("server_metadata_url") != GOOGLE_METADATA_URL:
        issues.append("server_metadata_url")
    return tuple(issues)


def login_ready():
    return not login_setup_issues()


def _login():
    if login_ready():
        _, _, provider = _google_login_settings()
        if provider:
            st.login(provider)
        else:
            st.login()


def clean_symbols(values, limit):
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(s for value in values if isinstance(value, str)
                              and (s := normalize_symbol(value))))[:limit]


def profile_values(row=None):
    if row is None:
        return {"watchlist": DEFAULT_WATCHLIST.copy(), "recent_searches": []}
    return {"watchlist": clean_symbols(row[0], MAX_WATCHLIST),
            "recent_searches": clean_symbols(row[1], MAX_RECENT)}


def apply_operations(profile, operations):
    """Operations update only the requested list; searches are newest first."""
    result = profile_values((profile["watchlist"], profile["recent_searches"]))
    for operation in operations:
        kind, value = operation["kind"], operation.get("value")
        if kind == "watchlist":
            result["watchlist"] = clean_symbols(value, MAX_WATCHLIST)
        elif kind == "search":
            symbol = normalize_symbol(value)
            if symbol:
                result["recent_searches"] = [symbol] + [s for s in result["recent_searches"] if s != symbol][:MAX_RECENT - 1]
        elif kind == "clear_recent":
            result["recent_searches"] = []
        else:
            raise ValueError("Unsupported account-list operation")
    return result


class AccountStore:
    """Parameterized owner queries; row locks avoid lost cross-device updates."""
    SELECT = "SELECT watchlist, recent_searches FROM buyntiq_private.preferences WHERE owner_key = %s"
    INSERT = "INSERT INTO buyntiq_private.preferences (owner_key, watchlist, recent_searches) VALUES (%s, %s, %s) ON CONFLICT (owner_key) DO NOTHING"
    UPDATE = "UPDATE buyntiq_private.preferences SET watchlist = %s, recent_searches = %s, updated_at = now() WHERE owner_key = %s"

    def __init__(self, database_url, connect=None):
        self.database_url = database_url
        self._connector = connect

    def _connect(self):
        if self._connector:
            return self._connector()
        import psycopg
        # Compatible with Supabase transaction pooling; encryption is required.
        return psycopg.connect(self.database_url, connect_timeout=5, sslmode="require",
                               prepare_threshold=None)

    @staticmethod
    def _validate_owner(owner):
        if not isinstance(owner, str) or len(owner) != 64 or any(c not in "0123456789abcdef" for c in owner):
            raise ValueError("A verified account key is required")

    def load(self, owner):
        self._validate_owner(owner)
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '5s'")
            row = connection.execute(self.SELECT, (owner,)).fetchone()
            return profile_values(row)

    def update(self, owner, operations):
        from psycopg.types.json import Jsonb
        self._validate_owner(owner)
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout = '5s'")
            connection.execute(self.INSERT, (owner, Jsonb(DEFAULT_WATCHLIST), Jsonb([])))
            row = connection.execute(self.SELECT + " FOR UPDATE", (owner,)).fetchone()
            result = apply_operations(profile_values(row), operations)
            connection.execute(self.UPDATE, (Jsonb(result["watchlist"]), Jsonb(result["recent_searches"]), owner))
            return result


def _store():
    url = _settings("accounts").get("database_url")
    if not url:
        raise ValueError("Account storage is not configured")
    return AccountStore(url)


def _set_profile(profile):
    changed = st.session_state.watchlist != profile["watchlist"]
    st.session_state.watchlist = profile["watchlist"]
    st.session_state.recent_searches = profile["recent_searches"]
    if changed:
        # The editor is recreated with the newly loaded values on its next run.
        st.session_state.pop("home_watchlist_input", None)


def sync_session(force=False):
    """Bind state to the verified account before rendering any user widgets."""
    identity = current_identity()
    owner = identity.key if identity else "guest"
    previous = st.session_state.get("_account_owner")
    if previous is not None and previous != owner:
        # Clear prior holdings, analyses, pending writes and widget drafts on
        # account changes. Never carry one account's pending writes to another.
        st.session_state.clear()
        from buyntiq.state import initialize
        initialize()
    st.session_state._account_owner = owner
    st.session_state.setdefault("_account_pending", [])
    st.session_state.setdefault("_account_last_attempt", 0.)
    st.session_state.setdefault("_account_loaded", False)
    if not identity:
        return
    due = time.time() - st.session_state._account_last_attempt >= 60
    if not force and not due:
        return
    st.session_state._account_last_attempt = time.time()
    try:
        store = _store()
        pending = st.session_state._account_pending
        profile = store.update(owner, pending) if pending else store.load(owner)
        _set_profile(profile)
        st.session_state._account_pending = []
        st.session_state._account_loaded = True
        st.session_state._account_error = None
    except Exception:
        # Never expose the database DSN or authentication details in the UI.
        st.session_state._account_error = "Your saved lists could not sync. Changes in this session are kept here until you retry."


def _queue(operation):
    sync_session()
    current = {"watchlist": st.session_state.watchlist,
               "recent_searches": st.session_state.recent_searches}
    _set_profile(apply_operations(current, [operation]))
    identity = current_identity()
    if identity:
        pending = list(st.session_state._account_pending)
        pending.append(operation)
        st.session_state._account_pending = pending
        sync_session(force=True)


def save_watchlist(text):
    _queue({"kind": "watchlist", "value": parse_symbols(text, MAX_WATCHLIST)})


def remember_current_search():
    symbol = normalize_symbol(st.session_state.get("_research_symbol", st.session_state.research_symbol))
    if symbol:
        _queue({"kind": "search", "value": symbol})


def clear_recent():
    _queue({"kind": "clear_recent"})


def storage_caption():
    if not current_identity():
        return "Sign in to save your watchlist and recent searches across devices. Guest lists last for this session."
    if st.session_state.get("_account_error"):
        return "Sync is unavailable. Your latest changes are currently kept only in this session."
    return "Watchlist and recent searches are saved to your account."


def _logout():
    st.session_state.clear()
    st.logout()


def render_account_menu():
    # The app may use Streamlit's light base theme under its dark custom CSS.
    # Scope overrides to the account trigger and its portalled popup.
    st.html("""<style>
    .st-key-account_control [data-testid="stPopover"] button {
      background:#000000!important; color:#ffffff!important;
      border:1px solid #606060!important; border-radius:7px!important;
      min-height:42px; font-weight:600;
    }
    .st-key-account_control [data-testid="stPopover"] button p,
    .st-key-account_control [data-testid="stPopover"] button svg {color:#ffffff!important;}
    .st-key-account_control [data-testid="stPopover"] button:hover {
      background:#111111!important; border-color:#a0a0a0!important;
    }
    [data-testid="stPopoverBody"]:has(.st-key-account_panel) {
      background:#0c0c0c!important; color:#ffffff!important;
      border:1px solid #505050!important; color-scheme:dark;
    }
    [data-testid="stPopoverBody"] div:has(.st-key-account_panel) {background:transparent!important;}
    .st-key-account_panel p, .st-key-account_panel [data-testid="stText"] {color:#ffffff!important;}
    .st-key-account_panel [data-testid="stCaptionContainer"] p {color:#c8c8c8!important;}
    .st-key-account_panel button {background:#000000!important; color:#ffffff!important; border-color:#606060!important;}
    .st-key-account_panel button:hover:not(:disabled) {background:#161616!important; border-color:#a0a0a0!important;}
    .st-key-account_panel button:disabled {background:#171717!important; color:#bdbdbd!important; opacity:1;}
    .st-key-account_panel button:disabled p {color:#bdbdbd!important;}
    </style>""")
    identity = current_identity()
    with st.container(key="account_control"), st.popover("Account", width="stretch"), st.container(key="account_panel"):
        if identity:
            st.text(identity.name)
            if identity.email:
                st.caption(identity.email)
            if st.session_state.get("_account_error"):
                st.warning(st.session_state._account_error)
                st.button("Retry sync", on_click=sync_session, kwargs={"force": True}, width="stretch")
            else:
                st.caption("Your lists follow you across devices.")
            st.button("Log out", on_click=_logout, width="stretch")
        else:
            st.write("Keep your watchlist and recent searches when you return.")
            ready = login_ready()
            st.button("Sign in with Google", on_click=_login, disabled=not ready, width="stretch")
            if not ready:
                st.caption("Google sign-in setup is incomplete. You can continue browsing as a guest.")
