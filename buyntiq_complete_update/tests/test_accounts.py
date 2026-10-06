"""Identity/ownership and session tests. Google and hosted Postgres are mocked."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest
from buyntiq import accounts, state

ROOT = Path(__file__).resolve().parents[1]


def identity(subject="alice", **claims):
    return accounts.google_identity({"is_logged_in": True,
        "iss": "https://accounts.google.com", "sub": subject,
        "email": subject + "@example.com", **claims})


def test_owner_uses_verified_google_subject_and_not_email():
    assert identity("alice", email="old@example.com").key == identity("alice", email="new@example.com").key
    assert identity("alice").key != identity("bob").key
    assert identity("alice", iss="accounts.google.com").key == identity("alice").key
    assert len(identity().key) == 64
    for claims in [{"is_logged_in": False}, {"iss": "https://untrusted.example"},
                   {"sub": None}, {"sub": ""}, {"sub": ["alice"]}]:
        assert identity(**claims) is None


def test_lists_are_normalized_bounded_and_can_be_cleared():
    profile = accounts.apply_operations(accounts.profile_values(), [
        {"kind": "watchlist", "value": [" aapl ", "brk.b", "AAPL", "<script>", 123]},
        *[{"kind": "search", "value": "S" + str(i)} for i in range(20)],
        {"kind": "search", "value": "s17"}])
    assert profile["watchlist"] == ["AAPL", "BRK-B"]
    assert profile["recent_searches"] == ["S17", "S19", "S18", "S16", "S15", "S14", "S13", "S12", "S11", "S10", "S9", "S8"]
    assert accounts.apply_operations(profile, [{"kind": "watchlist", "value": []},
        {"kind": "clear_recent"}]) == {"watchlist": [], "recent_searches": []}
    assert len(accounts.clean_symbols(["S" + str(i) for i in range(40)], 30)) == 30


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.fail = False

    def load(self, owner):
        self.calls.append(("load", owner))
        if self.fail:
            raise RuntimeError("private-database-password")
        return deepcopy(self.rows.get(owner, accounts.profile_values()))

    def update(self, owner, operations):
        result = accounts.apply_operations(self.load(owner), operations)
        self.rows[owner] = deepcopy(result)
        return result


class Session(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


@pytest.fixture
def session(monkeypatch):
    current = SimpleNamespace(user=identity())
    values, store = Session(), FakeStore()
    monkeypatch.setattr(accounts.st, "session_state", values)
    monkeypatch.setattr(accounts, "current_identity", lambda: current.user)
    monkeypatch.setattr(accounts, "_store", lambda: store)
    state.initialize()
    accounts.sync_session()
    return current, values, store


def test_failed_sync_keeps_edits_and_retry_preserves_other_device_changes(session):
    current, values, store = session
    store.rows[current.user.key] = {"watchlist": ["MSFT"], "recent_searches": ["NVDA"]}
    store.fail = True
    accounts.save_watchlist("AMD, AAPL")
    assert values.watchlist == ["AMD", "AAPL"]
    assert len(values._account_pending) == 1
    assert "private-database-password" not in values._account_error
    assert "only in this session" in accounts.storage_caption()
    store.fail = False
    accounts.sync_session(force=True)
    assert values.recent_searches == ["NVDA"]
    assert store.rows[current.user.key]["watchlist"] == ["AMD", "AAPL"]
    assert values._account_pending == [] and not values._account_error


def test_account_switch_discards_prior_private_state_and_pending_writes(session):
    current, values, store = session
    alice = current.user
    store.fail = True
    accounts.save_watchlist("AMD")
    values.update(builder_result={"secret": "alice holdings"}, review_rows=["alice"],
                  home_watchlist_input="alice draft", extra_forecasts={"alice": True})
    current.user = identity("bob")
    store.fail = False
    store.rows[current.user.key] = {"watchlist": ["TSLA"], "recent_searches": ["META"]}
    accounts.sync_session()
    assert values.watchlist == ["TSLA"] and values.recent_searches == ["META"]
    assert values.builder_result is None and values._account_pending == []
    assert "home_watchlist_input" not in values and "extra_forecasts" not in values
    assert alice.key not in store.rows
    current.user = None
    accounts.sync_session()
    assert values.watchlist == accounts.DEFAULT_WATCHLIST
    assert values.recent_searches == []


def test_guest_changes_never_reach_database_and_logout_clears_state(session, monkeypatch):
    current, values, store = session
    current.user = None
    accounts.sync_session()
    before = list(store.calls)
    accounts.save_watchlist("AMD")
    values._research_symbol = "nvda"
    accounts.remember_current_search()
    assert values.watchlist == ["AMD"] and values.recent_searches == ["NVDA"]
    assert store.calls == before
    called = []
    monkeypatch.setattr(accounts.st, "logout", lambda: called.append(True))
    accounts._logout()
    assert values == {} and called == [True]


def test_database_commands_are_owner_scoped_and_transactional():
    class Connection:
        def __init__(self):
            self.queries = []
            self.exits = []
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            self.exits.append(exc[0])
        def execute(self, sql, params=()):
            self.queries.append((sql, params))
            return self
        def fetchone(self):
            return ["MSFT"], ["META"]
    connection = Connection()
    store = accounts.AccountStore("unused", connect=lambda: connection)
    owner = identity().key
    assert store.update(owner, [{"kind": "search", "value": "NVDA"}]) == {
        "watchlist": ["MSFT"], "recent_searches": ["NVDA", "META"]}
    assert connection.queries[2] == (store.SELECT + " FOR UPDATE", (owner,))
    assert connection.queries[3][1][-1] == owner
    assert connection.queries[3][1][0].obj == ["MSFT"]
    assert connection.exits == [None]
    with pytest.raises(ValueError):
        store.load("' OR 1=1 --")
    with pytest.raises(ValueError):
        store.update(owner, [{"kind": "invalid"}])
    assert connection.exits[-1] is ValueError


def button(app, label):
    return next(b for b in app.button if b.label == label)


def test_saved_watchlist_loads_in_new_session_and_empty_list_stays_empty(monkeypatch):
    monkeypatch.chdir(ROOT)
    store, alice = FakeStore(), identity()
    monkeypatch.setattr(accounts, "current_identity", lambda: alice)
    monkeypatch.setattr(accounts, "_store", lambda: store)
    first = AppTest.from_file(str(ROOT / "app.py")).run()
    first.text_input(key="home_watchlist_input").set_value("AMD, NVDA")
    button(first, "Save watchlist").click().run()
    assert not first.exception
    assert first.session_state["watchlist"] == ["AMD", "NVDA"]
    second = AppTest.from_file(str(ROOT / "app.py")).run()
    assert second.session_state["watchlist"] == ["AMD", "NVDA"]
    second.text_input(key="home_watchlist_input").set_value("")
    button(second, "Save watchlist").click().run()
    third = AppTest.from_file(str(ROOT / "app.py")).run()
    assert not third.exception and third.session_state["watchlist"] == []


def test_research_callback_and_recent_shortcuts_persist_and_clear(monkeypatch):
    monkeypatch.chdir(ROOT)
    store, alice = FakeStore(), identity()
    monkeypatch.setattr(accounts, "current_identity", lambda: alice)
    monkeypatch.setattr(accounts, "_store", lambda: store)
    def unavailable(*args, **kwargs):
        raise ValueError("Test provider unavailable")
    monkeypatch.setattr("buyntiq.analytics.analyze", unavailable)
    app = AppTest.from_file(str(ROOT / "app.py")).run()
    app.switch_page("views/research.py").run()
    app.text_input(key="_research_symbol").set_value("NVDA")
    button(app, "Analyze stock").click().run()
    assert not app.exception
    assert app.session_state["recent_searches"] == ["NVDA"]
    assert store.rows[alice.key]["recent_searches"] == ["NVDA"]
    app.switch_page("views/home.py").run()
    app.button(key="home_recent_NVDA").click().run()
    assert not app.exception and app.text_input(key="_research_symbol").value == "NVDA"
    button(app, "Clear recent searches").click().run()
    assert not app.exception and store.rows[alice.key]["recent_searches"] == []


def test_missing_secrets_leave_guest_pages_available(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(accounts, "current_identity", lambda: None)
    monkeypatch.setattr(accounts, "_settings", lambda _: {})
    app = AppTest.from_file(str(ROOT / "app.py")).run()
    assert not app.exception
    assert not any(b.label == "Sign in with Google" for b in app.button)
    app.text_input(key="home_watchlist_input").set_value("AMD")
    button(app, "Save watchlist").click().run()
    assert not app.exception and app.session_state["watchlist"] == ["AMD"]


def google_settings(named=False):
    # Deliberately fake test credentials; no network request is made.
    shared = {"redirect_uri": "http://localhost:8501/oauth2callback",
              "cookie_secret": "test-cookie-value-never-for-deployment"}
    provider = {"client_id": "123456789-test.apps.googleusercontent.com",
                "client_secret": "test-google-value-never-for-deployment",
                "server_metadata_url": accounts.GOOGLE_METADATA_URL}
    return {**shared, "google": provider} if named else {**shared, **provider}


@pytest.mark.parametrize("named", [False, True])
def test_google_login_button_calls_the_configured_provider(monkeypatch, named):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(accounts, "current_identity", lambda: None)
    monkeypatch.setattr(accounts, "_settings", lambda section: google_settings(named) if section == "auth" else {})
    called = []
    monkeypatch.setattr(accounts.st, "login", lambda *args: called.append(args))
    app = AppTest.from_file(str(ROOT / "app.py")).run()
    assert not app.exception and not button(app, "Sign in with Google").disabled
    button(app, "Sign in with Google").click().run()
    assert not app.exception
    assert called == [("google",) if named else ()]


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("key", ["redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"])
def test_each_missing_google_setting_disables_login_without_leaking_values(monkeypatch, named, key):
    settings = google_settings(named)
    section = settings["google"] if named and key in ("client_id", "client_secret", "server_metadata_url") else settings
    section.pop(key)
    monkeypatch.setattr(accounts, "_settings", lambda _: settings)
    assert not accounts.login_ready()
    assert accounts.login_setup_issues() == (key,)
    monkeypatch.setattr(accounts.st, "login", lambda *args: pytest.fail("Incomplete login must not start"))
    accounts._login()


@pytest.mark.parametrize("key,value", [
    ("redirect_uri", "https://YOUR-APP.streamlit.app/oauth2callback"),
    ("cookie_secret", "REPLACE_WITH_A_LONG_RANDOM_SECRET"),
    ("client_id", "YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com"),
    ("client_secret", "YOUR_GOOGLE_CLIENT_SECRET"),
    ("client_secret", "   "), ("client_secret", 123),
    ("server_metadata_url", "https://untrusted.example/.well-known/openid-configuration"),
])
def test_example_or_invalid_settings_do_not_enable_login(monkeypatch, key, value):
    settings = google_settings()
    settings[key] = value
    monkeypatch.setattr(accounts, "_settings", lambda _: settings)
    assert accounts.login_setup_issues() == (key,)
    assert not accounts.login_ready()


def test_supabase_settings_alone_do_not_enable_google(monkeypatch):
    monkeypatch.setattr(accounts, "_settings", lambda section: {"database_url": "postgresql://example.invalid"} if section == "accounts" else {})
    assert not accounts.login_ready()
    assert len(accounts.login_setup_issues()) == 5
