from pathlib import Path
from types import SimpleNamespace
from buyntiq import accounts, billing, state
import pytest
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.chdir(ROOT)
    owner = accounts.Identity('a'*64, 'Test user', 'test@example.com')
    monkeypatch.setattr(accounts, 'current_identity', lambda: owner)
    monkeypatch.setattr(accounts, '_settings', lambda _: {})
    monkeypatch.setattr(accounts, '_store', lambda: SimpleNamespace(
        load=lambda _: accounts.profile_values(),
        update=lambda owner, ops: accounts.apply_operations(accounts.profile_values(), ops)))
    monkeypatch.setattr(billing, 'access', lambda force=False: billing.Access(pro=True))
    monkeypatch.setattr(billing, '_store', lambda: SimpleNamespace(
        reserve=lambda *a: 'today', refund=lambda *a: None))
    # Offline workflow fixtures only; production initialize always disables demo.
    real_initialize = state.initialize
    def fixture_initialize():
        state.st.session_state.demo_mode = False
        real_initialize()
        state.st.session_state.demo_mode = True
        state.st.session_state.price_source_policy = "real-only-1"
    monkeypatch.setattr(state, 'initialize', fixture_initialize)
    test_app = AppTest.from_file(str(ROOT/"app.py"),default_timeout=120).run()
    return test_app


def test_separate_pages_and_navigation_do_not_fetch_market_data(app,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError("Navigation must not fetch data")
    monkeypatch.setattr("buyntiq.data.prices",forbidden)
    monkeypatch.setattr("buyntiq.data.company",forbidden)
    monkeypatch.setattr("buyntiq.data.universe",forbidden)
    assert not app.exception
    for page,button in [("research.py","Analyze stock"),("builder.py","Build portfolio"),("review.py","Review my portfolio")]:
        app.switch_page("views/"+page).run()
        assert not app.exception
        labels=[b.label for b in app.button]
        assert button in labels
        for other in ["Analyze stock","Build portfolio","Review my portfolio"]:
            assert (other in labels) == (other==button)


def test_research_and_results_survive_switching_pages(app):
    app.switch_page("views/research.py").run()
    next(b for b in app.button if b.label=="Analyze stock").click().run()
    assert not app.exception
    score=app.session_state["research_result"]["score"]
    app.switch_page("views/home.py").run()
    app.switch_page("views/research.py").run()
    assert not app.exception
    assert app.session_state["research_result"]["score"] == score
    assert app.text_input[0].value == "AAPL"
    assert not any(t.label == "Demo data" for t in app.toggle)


def test_review_workflow_and_download(app):
    app.switch_page("views/review.py").run()
    next(b for b in app.button if b.label=="Load example").click().run()
    next(b for b in app.button if b.label=="Review my portfolio").click().run(timeout=120)
    assert not app.exception
    assert len(app.session_state["review_result"]["table"]) == 3
    assert len(app.get("download_button")) == 1
    app.switch_page("views/home.py").run()
    app.switch_page("views/review.py").run()
    assert len(app.session_state["review_rows"]) == 3
    assert not app.exception


def test_builder_workflow(app):
    app.switch_page("views/builder.py").run()
    app.selectbox[0].set_value("My symbols")
    app.text_area[0].set_value("AAPL, MSFT, NVDA, AMD")
    next(b for b in app.button if b.label=="Build portfolio").click().run(timeout=120)
    assert not app.exception
    result=app.session_state["builder_result"]
    assert 0 < len(result["table"]) <= 4
    assert (result["table"]["ML forecast"] > 0).all()
    assert result["requested"]==5
    assert app.warning
    projection = next(r for r in app.radio if r.label == 'Projection allocation')
    assert projection.value == 'Whole shares + cash'
    projection.set_value('Fractional target weights').run()
    assert not app.exception
    app.switch_page('views/review.py').run()
    assert not any(r.label == 'Projection allocation' for r in app.radio)
