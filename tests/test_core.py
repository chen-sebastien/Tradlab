import json,time,uuid,concurrent.futures
import pytest
from fastapi.testclient import TestClient
from backend.db import transaction,get,put,canonical
from backend.config import credentials,DEFAULT_RULES
from backend.domain import Proposal,evaluate,Rejected,reconciliation,valid_signature,round_tick
from backend.market import parse_csv,demo_rows,quote
from backend.broker import paper,LiveBroker
from backend.app import app
from risk_service.app import authorize,consume,Request
from watchdog.main import inspect
from research.engine import backtest,features,train
from research.worker import run_one

def make(quantity=50,symbol='0050',side='BUY',price=None):
    if price is None:
        with transaction() as t: price=round_tick(quote(t,symbol)['close']*1.02,get(t,'rules')['whitelist'][symbol])
    oid=str(uuid.uuid4());p=Proposal(symbol=symbol,quantity=quantity,side=side,price=price).model_dump()
    with transaction() as t:
        t.run('INSERT INTO orders(id,idem,payload,status,created,bar) VALUES(?,?,?,?,?,?)',(oid,oid,canonical(p),'PROPOSED',time.time(),get(t,'cursor')))
    return oid,p

def approve(quantity=50,**kw):
    oid,p=make(quantity,**kw);return authorize(Request(id=oid))

def fill(oid,idx=61):
    consume(Request(id=oid))
    with transaction() as t:
        paper.accept(t,t.one('SELECT * FROM orders WHERE id=?',(oid,)))
        put(t,'cursor',idx);put(t,'virtual_date',quote(t,'0050')['date']);paper.match(t)

def test_legal_partial_cancel_and_idempotent_fill():
    o=approve(90);assert o['status']=='APPROVED';assert valid_signature(o)
    fill(o['id'])
    with transaction() as t:
        o=t.one('SELECT * FROM orders WHERE id=?',(o['id'],));assert o['status']=='PARTIAL';assert 0<o['filled']<90
        before=get(t,'cash');paper.match(t);assert get(t,'cash')==before
        assert reconciliation(t)==[]
        paper.cancel(t,o);assert t.one('SELECT reserved FROM orders WHERE id=?',(o['id'],))['reserved']==0
        assert reconciliation(t)==[]

def test_over_order_and_oversell():
    assert 'PER_ORDER' in approve(200)['reason']
    assert 'OVERSELL' in approve(10,side='SELL')['reason']

def test_cash_stale_closed_units_missing():
    with transaction() as t:
        put(t,'cash',1);t.run('UPDATE ledger SET delta=1');put(t,'peak',1);put(t,'day_start',1)
        rules=get(t,'rules');rules['per_order']=100000;rules['per_symbol']=100000;rules['total']=100000;put(t,'rules',rules)
    assert 'CASH' in approve(50)['reason']
    with transaction() as t: put(t,'virtual_date','1900-01-01')
    assert 'STALE_QUOTE' in approve()['reason']
    with transaction() as t: put(t,'virtual_date',quote(t,'0050')['date']);put(t,'session_open',False)
    assert 'MARKET_CLOSED' in approve()['reason']

def test_concurrent_reservations_cannot_exceed_symbol_limit():
    ids=[make(100)[0] for _ in range(6)]
    with concurrent.futures.ThreadPoolExecutor(6) as pool: results=list(pool.map(lambda oid:authorize(Request(id=oid)),ids))
    assert sum(o['status']=='APPROVED' for o in results)==3
    assert any('SYMBOL_EXPOSURE' in (o['reason'] or '') for o in results)

def test_total_exposure_reservation():
    with transaction() as t:
        r=get(t,'rules');r['total']=.06;put(t,'rules',r)
    assert approve(100)['status']=='APPROVED'
    assert 'TOTAL_EXPOSURE' in approve(100,symbol='006208')['reason']

def test_tamper_expiry_one_use_rule_version():
    o=approve()
    with transaction() as t:
        p=json.loads(o['payload']);p['quantity']=10;t.run('UPDATE orders SET payload=? WHERE id=?',(canonical(p),o['id']))
        assert not valid_signature(t.one('SELECT * FROM orders WHERE id=?',(o['id'],)))
    with pytest.raises(Exception):consume(Request(id=o['id']))
    o=approve()
    with transaction() as t: t.run('UPDATE orders SET expires=0 WHERE id=?',(o['id'],))
    with pytest.raises(Exception):consume(Request(id=o['id']))
    o=approve();assert consume(Request(id=o['id']))['consumed'];assert consume(Request(id=o['id']))['idempotent']

@pytest.mark.parametrize('service',['risk','watchdog'])
def test_heartbeat_fails_closed(service):
    with transaction() as t: t.run('UPDATE heartbeats SET time=0 WHERE service=?',(service,))
    assert ('HEARTBEAT_'+service.upper()) in approve()['reason']

def test_watchdog_reconciliation_and_persistent_lock():
    with transaction() as t:
        put(t,'cash',99000);inspect(t);assert get(t,'locked') is True
    from backend.db import engine
    engine.dispose()
    with transaction() as t: assert get(t,'locked') is True;assert t.one("SELECT * FROM alerts WHERE code='RECONCILIATION'")

def test_audit_chain_tamper():
    with transaction() as t:
        t.run("UPDATE audit SET body='tampered' WHERE id=1");inspect(t);assert get(t,'locked')
        assert t.one("SELECT * FROM alerts WHERE code='AUDIT_CHAIN'")

def test_risk_stop_does_not_block_cancel_or_sell():
    o=approve(10);fill(o['id'])
    with transaction() as t: put(t,'stop_risk',True)
    assert 'LOCKED' in approve()['reason'];assert approve(5,side='SELL',price=45)['status']=='APPROVED'

def test_daily_loss_and_drawdown():
    with transaction() as t: put(t,'day_start',102000)
    assert 'DAILY_LOSS' in approve()['reason']
    with transaction() as t:put(t,'day_start',100000);put(t,'peak',106000)
    assert 'DRAWDOWN' in approve()['reason']
    with transaction() as t: assert get(t,'locked')

def test_unfilled_limit_and_restart():
    o=approve(50,price=45);fill(o['id'])
    from backend.db import engine
    engine.dispose()
    with transaction() as t:
        assert t.one('SELECT filled FROM orders WHERE id=?',(o['id'],))['filled']==0
        assert reconciliation(t)==[]

def test_csv_validation():
    header='date,open,high,low,close,volume\n'
    good='2024-01-02,10,11,9,10,100\n2024-01-03,10,12,9,11,200'
    assert len(parse_csv(header+good))==2
    for bad in [good.replace('2024-01-03','2024-01-02'),good.replace(',12,',',8,'),good.replace(',10,11,',',NaN,11,'),good.replace(',200',',-1')]:
        with pytest.raises(ValueError):parse_csv(header+bad)

def test_backtest_isolated_and_next_open():
    with transaction() as t: before=get(t,'cash')
    rows=demo_rows();r=backtest(rows,DEFAULT_RULES)
    assert r['strategy']['curve'];assert r['benchmark']['trades'][0]['date']==rows[20]['date']
    first=r['strategy']['trades'][0];bar=next(b for b in rows if b['date']==first['date'])
    assert first['price']==pytest.approx(bar['open']*1.001)
    with transaction() as t:assert get(t,'cash')==before

def test_research_reproducible_and_gap():
    rows=demo_rows();a=train(rows,'version','job','code');b=train(rows,'version','job','code')
    assert a['score']==b['score'];assert a['folds']==b['folds'];assert all(f['gap']==5 for f in a['folds'])
    assert all(f['train'][1]<f['validation'][0] for f in a['folds'])
    assert train(rows[:100],'short','job','code')['skipped']

def test_auth_origin_live_and_validation():
    c=TestClient(app);auth={'Authorization':'Bearer '+credentials()['client']}
    assert c.post('/api/mode',json={'mode':'LIVE'}).status_code==401
    assert c.post('/api/mode',json={'mode':'LIVE'},headers={**auth,'Origin':'https://evil.example'}).status_code==403
    assert c.post('/api/mode',json={'mode':'LIVE'},headers=auth).status_code==403
    assert c.post('/api/proposals',json={'approved':True},headers={**auth,'Idempotency-Key':'abcdefgh'}).status_code==422
    with pytest.raises(RuntimeError):LiveBroker()

def test_dataset_content_version_cannot_be_reset_by_renaming_source():
    from backend.market import save_dataset
    with transaction() as t:
        a=save_dataset(t,'0050',demo_rows(99),'source A')
        b=save_dataset(t,'0050',demo_rows(99),'source B')
        assert a==b

def test_day_order_expires_and_releases_remaining():
    o=approve(90);fill(o['id'])
    with transaction() as t:
        put(t,'cursor',62);put(t,'virtual_date',quote(t,'0050')['date']);paper.match(t)
        final=t.one('SELECT * FROM orders WHERE id=?',(o['id'],))
        assert final['status']=='CANCELLED';assert final['reserved']==0;assert final['filled']==56
        assert reconciliation(t)==[]

def test_model_cannot_activate_before_training_end():
    from backend.app import approve_model
    with transaction() as t:
        t.run('INSERT INTO models(id,job_id,dataset,report,artifact) VALUES(?,?,?,?,?)',
            ('test-model','test-job','dataset',canonical({'eligible':True,'train':['2024-01-01','2024-12-24'],'available_after':'2024-12-31'}),'unused'))
    with pytest.raises(ValueError,match='訓練結束日'):approve_model('test-model')
    with transaction() as t:put(t,'virtual_date','2025-01-02')
    assert approve_model('test-model')['ok']
    with transaction() as t:assert get(t,'active_model')=='test-model'

def test_realized_model_outcomes_only_after_closed_entry_lots():
    from research.monitor import realized
    oid,p=make(10)
    with transaction() as t:
        p['model_id']='test-model';t.run('UPDATE orders SET payload=? WHERE id=?',(canonical(p),oid))
    authorize(Request(id=oid));fill(oid)
    sale=approve(10,side='SELL',price=45);fill(sale['id'],62)
    with transaction() as t:
        monitor=realized(t,'test-model')
        assert monitor['closed_entry_lots']==1;assert monitor['status']=='N/A';assert monitor['mean_net_return'] is None

def test_timeout_after_consume_is_unknown_until_reconciled(monkeypatch):
    import importlib
    from fastapi import HTTPException
    api=importlib.import_module('backend.app');o=approve()
    def lose_response(path,oid):
        consume(Request(id=oid))
        raise HTTPException(503,'Injected lost response after durable consume')
    monkeypatch.setattr(api,'request_risk',lose_response)
    with pytest.raises(HTTPException):api.execute(o['id'])
    with transaction() as t:
        assert t.one('SELECT status FROM orders WHERE id=?',(o['id'],))['status']=='UNKNOWN'
        assert not t.all('SELECT * FROM fills')
    assert api.reconcile_order(o['id'])['ok']
    assert api.execute(o['id'])['status']=='ACCEPTED'

def test_schedule_deduplicates_same_dataset():
    from research.worker import schedule
    with transaction() as t:
        put(t,'schedule',{'enabled':True,'interval':60,'last':0});schedule(t)
        first=t.all('SELECT id FROM jobs');assert len(first)==3
        put(t,'schedule',{'enabled':True,'interval':60,'last':0});schedule(t)
        assert t.all('SELECT id FROM jobs')==first

def test_backup_restore_on_isolated_database(monkeypatch,tmp_path):
    import sys
    from scripts.backup import main
    from backend.db import engine
    target=tmp_path/'backup.sqlite3'
    monkeypatch.setattr(sys,'argv',['backup','backup',str(target)]);main()
    with transaction() as t:put(t,'cash',99999)
    engine.dispose()
    monkeypatch.setattr(sys,'argv',['backup','restore',str(target)]);main()
    with transaction() as t:assert get(t,'cash')==100000;assert reconciliation(t)==[]
