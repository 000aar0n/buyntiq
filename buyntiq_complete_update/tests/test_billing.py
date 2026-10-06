from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from buyntiq import accounts, billing, email_auth

class Session(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

@pytest.fixture
def ss(monkeypatch):
    values = Session()
    monkeypatch.setattr(billing.st, 'session_state', values)
    return values

def subscription():
    return dict(customer='cus_a', livemode=False, status='active', latest_invoice={'status':'paid'}, items={'data':[{'price':{'id':'price_a'}, 'current_period_end':2000}]})

@pytest.mark.parametrize('change', [{}, {'status':'trialing'}, {'status':'past_due'}, {'status':'canceled'}, {'customer':'cus_b'}, {'livemode':True}, {'latest_invoice':{'status':'open'}}, {'pause_collection':{'behavior':'void'}}, {'items':{'data':[]}}])
def test_only_paid_active_correct_customer_price_mode(change):
    sub=subscription();sub.update(change)
    assert billing.paid_access([sub],'cus_a',{'price_a'},False,1000).pro == (not change)
    assert not billing.paid_access([sub],'cus_a',{'price_wrong'},False,1000).pro
    assert not billing.paid_access([sub],'cus_a',{'price_a'},False,3000).pro

def test_cancel_at_period_end_keeps_paid_period():
    sub=subscription();sub['cancel_at_period_end']=True
    assert billing.paid_access([sub],'cus_a',{'price_a'},False,1000).pro

@pytest.mark.parametrize('feature',['build','review','forecasts'])
def test_free_cannot_run_paid_actions_even_demo(monkeypatch,ss,feature):
    ss.demo_mode=True
    owner=accounts.Identity('a'*64,'Alice','a@example.com')
    monkeypatch.setattr(billing,'_identity',lambda force=False:owner)
    monkeypatch.setattr(accounts,'current_identity',lambda:owner)
    monkeypatch.setattr(billing,'access',lambda force=False:billing.Access())
    monkeypatch.setattr(billing,'_store',lambda:pytest.fail('Must reject before usage'))
    with pytest.raises(billing.BillingError):
        with billing.action(feature): pytest.fail('Premium work ran')

def test_failed_computation_refunds_same_owner(monkeypatch,ss):
    owner=accounts.Identity('a'*64,'Alice','a@example.com');calls=[]
    monkeypatch.setattr(billing,'_identity',lambda force=False:owner)
    monkeypatch.setattr(accounts,'current_identity',lambda:owner)
    monkeypatch.setattr(billing,'access',lambda force=False:billing.Access())
    monkeypatch.setattr(accounts,'_settings',lambda _: {})
    store=SimpleNamespace(reserve=lambda *args:calls.append(('reserve',args)) or 'today', refund=lambda *args:calls.append(('refund',args)))
    monkeypatch.setattr(billing,'_store',lambda:store)
    with pytest.raises(RuntimeError):
        with billing.action('research') as access:
            assert not access.pro
            raise RuntimeError('provider failed')
    assert calls==[('reserve',(owner.key,'research',5)),('refund',(owner.key,'research','today'))]

def test_identity_expires_before_reservation(monkeypatch,ss):
    owner=accounts.Identity('a'*64,'Alice','a@example.com')
    monkeypatch.setattr(billing,'_identity',lambda force=False:owner)
    monkeypatch.setattr(accounts,'current_identity',lambda:None)
    monkeypatch.setattr(billing,'access',lambda force=False:billing.Access())
    with pytest.raises(billing.BillingError,match='expired'):
        with billing.action('research'): pytest.fail('Expired user ran')

def test_email_verified_subject_stable_and_revoked_session_cleared(monkeypatch,ss):
    monkeypatch.setattr(email_auth,'config',lambda:{'url':'https://example.supabase.co','key':'public'})
    user={'id':'11111111-1111-4111-8111-111111111111','email':'alice@example.com','email_confirmed_at':'date'}
    monkeypatch.setattr(email_auth,'_request',lambda *a,**k:user)
    ss._email_auth=email_auth._session({'access_token':'opaque','refresh_token':'refresh','expires_in':3600})
    first=email_auth.identity_claims(force=True)
    user['email']='new@example.com'
    assert email_auth.identity_claims(force=True)['key']==first['key']
    user['id']='22222222-2222-4222-8222-222222222222'
    assert email_auth.identity_claims(force=True) is None
    assert '_email_auth' not in ss

def test_email_login_clears_previous_holdings(monkeypatch,ss):
    monkeypatch.setattr(email_auth,'config',lambda:{'url':'https://example.supabase.co','key':'public'})
    def request(method,path,*a,**k):
        return {'id':'11111111-1111-4111-8111-111111111111','email':'a@example.com','email_confirmed_at':'date'} if path=='user' else {'access_token':'opaque','refresh_token':'refresh','expires_in':3600}
    monkeypatch.setattr(email_auth,'_request',request)
    ss.update(_email_pending='a@example.com',review_result={'private':'old'},_billing_checked='old')
    email_auth.verify_code('123456')
    assert ss.review_result is None and '_billing_checked' not in ss
    assert accounts.current_identity().email=='a@example.com'

def test_guest_pages_render_and_return_url_does_not_unlock(monkeypatch):
    root=Path(__file__).resolve().parents[1];monkeypatch.chdir(root)
    monkeypatch.setattr(accounts,'current_identity',lambda:None)
    monkeypatch.setattr(accounts,'_settings',lambda _: {})
    app=AppTest.from_file(str(root/'app.py')).run()
    for page in ['research','builder','review','plans','home']:
        app.switch_page('views/'+page+'.py').run()
        assert not app.exception
        if page in ['builder','review']:
            assert any('included with Buyntiq Pro' in v.value for v in app.info)
    app.query_params['checkout']='returned'
    app.switch_page('views/plans.py').run()
    assert not app.exception and not any('active' in s.value for s in app.success)

def test_subscription_cache_is_per_owner_and_force_bypasses(monkeypatch,ss):
    who=[accounts.Identity('a'*64,'Alice','a@example.com')];calls=[]
    monkeypatch.setattr(billing,'_identity',lambda force=False:who[0])
    monkeypatch.setattr(billing,'config',lambda:{'key':'sk_test_fake','price':'price_a','prices':{'price_a'},'live':False})
    monkeypatch.setattr(billing,'_store',lambda:SimpleNamespace(customer=lambda owner,live:'cus_a' if owner=='a'*64 else 'cus_b'))
    def rows(customer):
        calls.append(customer);sub=subscription();sub['items']['data'][0]['current_period_end']=billing.time.time()+1000;return [sub]
    monkeypatch.setattr(billing,'subscriptions',rows)
    assert billing.access().pro and billing.access().pro and len(calls)==1
    assert billing.access(force=True).pro and len(calls)==2
    who[0]=accounts.Identity('b'*64,'Bob','b@example.com')
    assert not billing.access().pro and calls[-1]=='cus_b'
    monkeypatch.setattr(billing,'subscriptions',lambda _:(_ for _ in ()).throw(RuntimeError('secret')))
    denied=billing.access(force=True)
    assert not denied.pro and not denied.verified and 'secret' not in denied.message


def test_email_code_ui_completes_without_widget_errors(monkeypatch):
    root=Path(__file__).resolve().parents[1];monkeypatch.chdir(root)
    monkeypatch.setattr(accounts,'_settings',lambda _: {})
    monkeypatch.setattr(email_auth,'config',lambda:{'url':'https://example.supabase.co','key':'public'})
    def request(method,path,*a,**k):
        if path=='user':return {'id':'11111111-1111-4111-8111-111111111111','email':'a@example.com','email_confirmed_at':'date'}
        if path=='verify':return {'access_token':'opaque','refresh_token':'refresh','expires_in':3600}
        return {}
    monkeypatch.setattr(email_auth,'_request',request)
    app=AppTest.from_file(str(root/'app.py')).run()
    next(v for v in app.text_input if v.label=='Email address').set_value('a@example.com')
    next(v for v in app.button if v.label=='Email me a sign-in code').click().run()
    next(v for v in app.text_input if v.label=='Sign-in code').set_value('123456')
    next(v for v in app.button if v.label=='Verify and sign in').click().run()
    assert not app.exception
    assert any(v.label=='Log out' for v in app.button)
    next(v for v in app.button if v.label=='Log out').click().run()
    assert not app.exception
    assert any(v.label=='Email me a sign-in code' for v in app.button)

@pytest.mark.parametrize('pro',[False,True])
def test_research_passes_actual_entitlement_to_ml(monkeypatch,pro):
    root=Path(__file__).resolve().parents[1];monkeypatch.chdir(root)
    owner=accounts.Identity('a'*64,'Alice','a@example.com');calls=[]
    monkeypatch.setattr(accounts,'current_identity',lambda:owner)
    monkeypatch.setattr(accounts,'_settings',lambda _: {})
    permit=billing.Access(pro=pro,expires_at=billing.time.time()+1000)
    monkeypatch.setattr(billing,'access',lambda force=False:permit)
    monkeypatch.setattr(billing,'_store',lambda:SimpleNamespace(reserve=lambda *a:'today',refund=lambda *a:None))
    def compute(*args,**kwargs):
        calls.append(kwargs);raise ValueError('Intentional provider failure after checking ML argument')
    monkeypatch.setattr('buyntiq.analytics.analyze',compute)
    app=AppTest.from_file(str(root/'app.py')).run()
    app.switch_page('views/research.py').run()
    next(v for v in app.button if v.label=='Analyze stock').click().run()
    assert not app.exception and calls[0]['include_ml'] is pro and calls[0]['demo'] is False
    for page,label in [('builder','Build portfolio'),('review','Review my portfolio')]:
        app.switch_page('views/'+page+'.py').run()
        assert not app.exception
        assert any(v.label==label for v in app.button) is pro

def test_checkout_reuses_pending_session_and_blocks_duplicate_subscription(monkeypatch):
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,*a):return self
        def fetchone(self):return ('cs_pending',)
    store=billing.BillingStore(connect=Connection)
    monkeypatch.setattr(store,'ensure_customer',lambda *a:'cus_a')
    monkeypatch.setattr(billing,'subscriptions',lambda _:[])
    calls=[]
    def stripe(method,path,*a,**k):
        calls.append((method,path))
        if path.endswith('/line_items'):return {'data':[{'price':{'id':'price_a'}}]}
        return {'status':'open','expires_at':billing.time.time()+1000,'url':'https://checkout.stripe.com/c/pay/test'}
    monkeypatch.setattr(billing,'_stripe',stripe)
    owner=accounts.Identity('a'*64,'Alice','a@example.com');settings={'live':False,'price':'price_a'}
    assert store.checkout(owner,settings)=='https://checkout.stripe.com/c/pay/test'
    assert all(method=='GET' for method,path in calls)
    monkeypatch.setattr(billing,'subscriptions',lambda _:[subscription()])
    with pytest.raises(billing.BillingError,match='already have'):
        store.checkout(owner,settings)


def test_free_research_result_renders_and_never_calls_forecast(monkeypatch):
    root=Path(__file__).resolve().parents[1];monkeypatch.chdir(root)
    owner=accounts.Identity('a'*64,'Alice','a@example.com')
    monkeypatch.setattr(accounts,'current_identity',lambda:owner)
    monkeypatch.setattr(accounts,'_settings',lambda _: {})
    monkeypatch.setattr(billing,'access',lambda force=False:billing.Access())
    monkeypatch.setattr(billing,'_store',lambda:SimpleNamespace(reserve=lambda *a:'today',refund=lambda *a:None))
    monkeypatch.setattr('buyntiq.model.forecast',lambda *a,**k:pytest.fail('Free must not calculate ML'))
    app=AppTest.from_file(str(root/'app.py')).run()
    # Explicit offline fixture, not a user-accessible production setting.
    from buyntiq import state
    original = state.initialize
    def fixture_initialize():
        state.st.session_state.demo_mode = False
        original()
        state.st.session_state.demo_mode = True
    monkeypatch.setattr(state, 'initialize', fixture_initialize)
    app.run()
    app.switch_page('views/research.py').run()
    next(v for v in app.button if v.label=='Analyze stock').click().run()
    assert not app.exception and app.session_state['research_result'] is not None
    assert app.session_state['research_result']['forecast'] is None
    assert any('ML forecasts is included' in v.value for v in app.info)
    app.switch_page('views/home.py').run()
    app.switch_page('views/research.py').run()
    assert not app.exception and app.session_state['research_result'] is not None

MONTH = 'price_1UNbQDGnhPJ6YaUQwD9TSsoM'
YEAR = 'price_1UNbRHGnhPJ6YaUQIhk0cHY9'

@pytest.fixture
def price_config(monkeypatch):
    settings={'secret_key':'sk_test_fixture', 'price_id':MONTH, 'annual_price_id':YEAR}
    monkeypatch.setattr(accounts,'_settings',lambda section:settings if section=='stripe' else {})
    return settings

def price_response(price_id, interval):
    return dict(id=price_id,active=True,livemode=False,currency='usd',unit_amount=1000,
                recurring={'interval':interval,'interval_count':1,'usage_type':'licensed'},
                billing_scheme='per_unit')

def test_monthly_and_annual_grant_identical_access(price_config):
    settings=billing.config()
    assert settings['options']=={'monthly':MONTH,'annual':YEAR}
    for price_id in [MONTH,YEAR]:
        sub=subscription();sub['items']['data'][0]['price']['id']=price_id
        assert billing.paid_access([sub],'cus_a',settings['prices'],False,1000).pro

@pytest.mark.parametrize('cycle,price_id,interval',[('monthly',MONTH,'month'),('annual',YEAR,'year')])
def test_interval_validated_before_checkout(monkeypatch,price_config,cycle,price_id,interval):
    value=price_response(price_id,interval)
    monkeypatch.setattr(billing,'_stripe',lambda *a,**k:value)
    assert billing.price_details(cycle)['id']==price_id
    value['recurring']['interval']='week'
    with pytest.raises(billing.BillingError,match='flat-rate'):
        billing.price_details(cycle)

@pytest.mark.parametrize('change',[{'livemode':True},{'currency':'eur'},{'unit_amount':0},{'billing_scheme':'tiered'},{'active':False},{'id':'price_untrusted'}])
def test_invalid_price_cannot_start_checkout(monkeypatch,price_config,change):
    value=price_response(MONTH,'month');value.update(change)
    monkeypatch.setattr(billing,'_stripe',lambda *a,**k:value)
    with pytest.raises(billing.BillingError):billing.price_details('monthly')

def test_unknown_cycle_and_unconfigured_annual_denied(price_config):
    with pytest.raises(billing.BillingError):billing.price_details('lifetime')
    price_config.pop('annual_price_id')
    with pytest.raises(billing.BillingError):billing.price_details('annual')
    assert billing.config()['options']=={'monthly':MONTH}

def test_switch_period_expires_old_checkout_and_uses_annual_price(monkeypatch,price_config):
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,*a):return self
        def fetchone(self):return ('cs_month',)
    store=billing.BillingStore(connect=Connection);calls=[]
    monkeypatch.setattr(store,'ensure_customer',lambda *a:'cus_a')
    monkeypatch.setattr(billing,'subscriptions',lambda _:[])
    def stripe(method,path,data=None,**kwargs):
        calls.append((method,path,data,kwargs))
        if path.endswith('/line_items'):return {'data':[{'price':{'id':MONTH}}]}
        if path.endswith('/expire'):return {'status':'expired'}
        if method=='GET':return {'status':'open','expires_at':billing.time.time()+1000,'url':'https://checkout.stripe.com/month'}
        return {'id':'cs_year','url':'https://checkout.stripe.com/year'}
    monkeypatch.setattr(billing,'_stripe',stripe)
    owner=accounts.Identity('a'*64,'Alice','a@example.com')
    assert store.checkout(owner,billing.config(),'annual').endswith('/year')
    assert calls[2][:2]==('POST','checkout/sessions/cs_month/expire')
    assert calls[3][2]['line_items[0][price]']==YEAR
    assert calls[3][2]['client_reference_id']==owner.key
    assert calls[3][3]['idempotency'].startswith('buyntiq-checkout-')

def test_plans_annual_selector_hides_monthly_checkout_link(monkeypatch,price_config):
    root=Path(__file__).resolve().parents[1];monkeypatch.chdir(root)
    owner=accounts.Identity('a'*64,'Alice','a@example.com');choices=[]
    monkeypatch.setattr(accounts,'current_identity',lambda:owner)
    monkeypatch.setattr(billing,'_stripe',lambda method,path,*a,**k:price_response(path.split('/')[-1],'month' if path.endswith(MONTH) else 'year'))
    def checkout(identity,settings,cycle):
        choices.append(cycle);return 'https://checkout.stripe.com/'+cycle
    monkeypatch.setattr(billing,'_store',lambda:SimpleNamespace(customer=lambda *a:None,usage=lambda *a:{},checkout=checkout))
    app=AppTest.from_file(str(root/'app.py')).run()
    app.switch_page('views/plans.py').run()
    next(b for b in app.button if b.label=='Subscribe to Pro').click().run()
    assert not app.exception and choices==['monthly']
    assert any(b.url.endswith('/monthly') for b in app.get('link_button'))
    app.radio(key='subscription_cycle').set_value('annual').run()
    assert not app.exception and not app.get('link_button')
    next(b for b in app.button if b.label=='Subscribe to Pro').click().run()
    assert not app.exception and choices==['monthly','annual']
    assert any(b.url.endswith('/annual') for b in app.get('link_button'))


def test_owner_pro_requires_exact_verified_key_and_revokes(monkeypatch, ss):
    owner = accounts.Identity('a' * 64, 'Owner', 'same@example.com')
    other = accounts.Identity('b' * 64, 'Other', 'same@example.com')
    plans = {'owner_account_keys': [owner.key]}
    monkeypatch.setattr(accounts, '_settings', lambda section: plans if section == 'plans' else {})
    monkeypatch.setattr(billing, '_identity', lambda force=False: owner)
    monkeypatch.setattr(billing, '_store', lambda: pytest.fail('Owner entitlement needs no Stripe lookup'))
    assert billing.access(force=True).status == 'owner'
    assert billing.access(force=True).pro
    assert not billing.owner_access(other)
    assert not billing.owner_access(None)
    plans['owner_account_keys'] = owner.key  # Reject malformed string allowlists.
    assert not billing.owner_access(owner)
    plans['owner_account_keys'] = []
    monkeypatch.setattr(billing, '_record_access', lambda result: None)
    assert not billing.access(force=True).pro


@pytest.mark.parametrize("status", ["owner", "active"])
def test_pro_actions_bypass_quotas(monkeypatch, ss, status):
    owner = accounts.Identity('a' * 64, 'Owner', 'owner@example.com')
    monkeypatch.setattr(accounts, '_settings', lambda section: {'owner_account_keys': [owner.key]} if section == 'plans' else {})
    monkeypatch.setattr(billing, '_identity', lambda force=False: owner)
    monkeypatch.setattr(accounts, 'current_identity', lambda: owner)
    monkeypatch.setattr(billing, "access", lambda force=False: billing.Access(pro=True, status=status))
    monkeypatch.setattr(billing, "_store", lambda: pytest.fail("Pro must not reserve quota"))
    for feature in ('research', 'build', 'review', 'forecasts'):
        with billing.action(feature) as result:
            assert result.pro and result.status == status
    assert billing.limit_for("research", True) is None


def test_live_session_removes_old_demo_results(ss):
    from buyntiq import state
    ss.update(demo_mode=True, price_source_policy='real-only-1',
              research_result={'demo': True}, builder_result={'demo': True})
    state.initialize()
    assert ss.demo_mode is False
    assert ss.research_result is None and ss.builder_result is None
