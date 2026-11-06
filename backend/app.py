import asyncio, json, time, uuid, hashlib
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from backend.config import ROOT,RISK_URL,ORIGINS,credentials
from backend.db import transaction,get,put,audit,canonical,heartbeat,alert
from backend.domain import Proposal,account,valid_signature,reconciliation,round_tick,expire
from backend.market import parse_csv,save_dataset,twse_month,quote,active
from backend.broker import paper
from backend.security import install_security

async def pulse():
    instance=str(uuid.uuid4())
    while True:
        with transaction() as t: heartbeat(t,'backend',instance)
        await asyncio.sleep(2)

@asynccontextmanager
async def life(app):
    task=asyncio.create_task(pulse()); yield; task.cancel()

app=FastAPI(title='TradingLab 研究與模擬 API',version='1.0.0',lifespan=life)
app.add_middleware(CORSMiddleware,allow_origins=list(ORIGINS),allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type','Idempotency-Key'])
install_security(app)

@app.exception_handler(ValueError)
async def value_error(req,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({'detail':str(exc)},status_code=422)

def order_view(o):
    if not o: return None
    o=dict(o);o['payload']=json.loads(o['payload']);o.pop('approval',None)
    return o

def model_available_after(t,model):
    report=json.loads(model['report'])
    if report.get('available_after'): return report['available_after']
    # Compatibility for earlier artifacts: training labels need five further sessions.
    future=t.all('SELECT date FROM bars WHERE dataset=? AND date>? ORDER BY date LIMIT 5',(model['dataset'],report['train'][1]))
    return future[-1]['date'] if len(future)==5 else '9999-12-31'

def request_risk(path,order_id):
    try:
        with httpx.Client(timeout=3) as client:
            response=client.post(RISK_URL+path,json={'id':order_id},headers={'Authorization':'Bearer '+credentials()['service']})
            response.raise_for_status(); return response.json()
    except httpx.HTTPStatusError as e: raise HTTPException(409,e.response.json().get('detail','風控拒絕'))
    except httpx.HTTPError: raise HTTPException(503,'風控服務無法確認；禁止新增風險，請檢查服務後核對狀態')

@app.get('/health')
def health(): return {'service':'backend','ok':True,'live':False}

@app.get('/api/state')
def state():
    with transaction() as t:
        expire(t)
        datasets=t.all('SELECT * FROM datasets ORDER BY received DESC')
        for d in datasets: d['metadata']=json.loads(d['metadata'])
        models=t.all('SELECT * FROM models')
        from research.monitor import realized
        for m in models:
            m['report']=json.loads(m['report']);m['report']['realized_performance']=realized(t,m['id']);m.pop('artifact',None)
        return dict(mode=get(t,'mode'),virtual_date=get(t,'virtual_date'),cursor=get(t,'cursor'),account=account(t),
            quotes=[quote(t,s) for s in active(t) if quote(t,s)],datasets=datasets,active=active(t),rules=get(t,'rules'),
            heartbeats=t.all('SELECT * FROM heartbeats'),server_time=time.time(),locked=get(t,'locked'),stop_risk=get(t,'stop_risk'),
            pause_strategy=get(t,'pause_strategy'),session_open=get(t,'session_open'),
            orders=[order_view(o) for o in t.all('SELECT * FROM orders ORDER BY created DESC LIMIT 200')],
            fills=t.all('SELECT f.*,json_extract(o.payload,\'$.symbol\') symbol,json_extract(o.payload,\'$.side\') side FROM fills f JOIN orders o ON o.id=f.order_id ORDER BY f.id DESC LIMIT 200'),
            alerts=t.all('SELECT * FROM alerts ORDER BY id DESC LIMIT 100'),audit=t.all('SELECT * FROM audit ORDER BY id DESC LIMIT 100'),
            jobs=t.all('SELECT id,kind,status,created,params,error,progress FROM jobs ORDER BY created DESC LIMIT 100'),models=models,
            active_model=get(t,'active_model'),schedule=get(t,'schedule'))

@app.get('/api/bars/{dataset}')
def bars(dataset:str):
    with transaction() as t: return t.all('SELECT * FROM bars WHERE dataset=? ORDER BY idx',(dataset,))

class Import(BaseModel):
    symbol: str
    csv: str = Field(max_length=15000000)
    source: str = Field(min_length=1,max_length=200)
    adjusted: bool=False

@app.post('/api/data/import')
def import_csv(body:Import):
    rows=parse_csv(body.csv)
    with transaction() as t:
        if body.symbol not in get(t,'rules')['whitelist']: raise ValueError('不支援的標的')
        return {'dataset':save_dataset(t,body.symbol,rows,body.source,'REPLAY',body.adjusted),'rows':len(rows),'warnings':['未驗證資料供應商、停牌與公司行動；日期依 CSV，不推定缺少日期為交易日']}

class Fetch(BaseModel): symbol:str; month:str
@app.post('/api/data/twse')
def fetch(body:Fetch):
    with transaction() as t:
        if body.symbol not in get(t,'rules')['whitelist']: raise ValueError('不支援的標的')
    try: rows=twse_month(body.symbol,body.month)
    except Exception as e: raise HTTPException(502,'TWSE 下載失敗，未切換或偽造資料：'+str(e)[:300])
    with transaction() as t: return {'dataset':save_dataset(t,body.symbol,rows,'TWSE STOCK_DAY 月日線；非即時','REPLAY'),'rows':len(rows)}

class Activate(BaseModel): dataset:str
@app.post('/api/data/activate')
def activate(body:Activate):
    with transaction() as t:
        if account(t)['positions'] or t.one("SELECT id FROM orders WHERE status IN ('APPROVED','SENT','ACCEPTED','PARTIAL','UNKNOWN')"):
            raise ValueError('請先撤單與模擬平倉後再切換資料集')
        d=t.one('SELECT * FROM datasets WHERE id=?',(body.dataset,))
        if not d: raise ValueError('資料集不存在')
        if json.loads(d['metadata'])['adjusted']: raise ValueError('還原資料僅供研究，不能啟用委託重播')
        first=t.one('SELECT * FROM bars WHERE dataset=? AND idx=0',(body.dataset,))
        put(t,'active',{d['symbol']:{'dataset':body.dataset}});put(t,'cursor',0);put(t,'virtual_date',first['date']);put(t,'mode',d['mode'])
        put(t,'day_start',account(t)['nav']); audit(t,'activate_dataset',{'id':body.dataset})
    return {'ok':True}

@app.post('/api/replay/step')
def step():
    try:
        response=httpx.get(RISK_URL+'/health',timeout=2)
        response.raise_for_status()
        if response.json().get('service')!='risk': raise ValueError('非預期風控服務')
    except (httpx.HTTPError,ValueError): raise HTTPException(503,'風控服務未確認在線，暫停重播成交')
    with transaction() as t:
        # Existing approved buys must not fill while any independent guard is unavailable.
        if not get(t,'session_open',True): raise ValueError('模擬交易時段已關閉')
        rules=get(t,'rules')
        for service in ('risk','watchdog'):
            h=t.one('SELECT * FROM heartbeats WHERE service=?',(service,))
            if not h or time.time()-h['time']>rules['heartbeat_ttl']: raise HTTPException(409,service+' 心跳過期，重播成交暫停')
        if reconciliation(t): raise ValueError('帳務不一致')
        next_idx=get(t,'cursor')+1; days=[]
        for s,v in active(t).items():
            b=t.one('SELECT * FROM bars WHERE dataset=? AND idx=?',(v['dataset'],next_idx))
            if not b: raise ValueError('已到資料尾端')
            days.append(b['date'])
        if len(set(days))!=1: raise ValueError('多標的交易日期不一致，禁止推進')
        put(t,'day_start',account(t)['nav']);put(t,'cursor',next_idx);put(t,'virtual_date',days[0])
        # Stop-new-risk also prevents pending buys from filling; cancellation is separate.
        if not get(t,'locked') and not get(t,'stop_risk'): paper.match(t)
        else:
            # Conservatively keep pending orders untouched; risk-reducing sells are handled separately.
            paper.match(t, only_sells=True)
        a=account(t);put(t,'peak',max(a['nav'],get(t,'peak')))
        if a['drawdown']>=rules['drawdown']: alert(t,'DRAWDOWN',{'drawdown':a['drawdown']})
        audit(t,'replay_step',{'cursor':next_idx,'date':days[0]})
    return {'ok':True}

@app.post('/api/proposals')
def proposal(body:Proposal,idempotency_key:str=Header(alias='Idempotency-Key',min_length=8,max_length=100)):
    p=body.model_dump()
    with transaction() as t:
        if p['model_id']:
            model=t.one('SELECT * FROM models WHERE id=?',(p['model_id'],))
            if not model or get(t,'active_model')!=p['model_id']: raise ValueError('只能使用目前人工核准模型')
            if get(t,'virtual_date')<=model_available_after(t,model): raise ValueError('重播時間不得早於模型訓練標籤完成時間，避免未來資料洩漏')
        existing=t.one('SELECT * FROM orders WHERE idem=?',(idempotency_key,))
        if existing:
            if json.loads(existing['payload'])!=p: raise HTTPException(409,'冪等鍵已綁定不同訂單內容')
            order_id=existing['id']
        else:
            order_id=str(uuid.uuid4())
            t.run('INSERT INTO orders(id,idem,payload,status,created,bar) VALUES(?,?,?,?,?,?)',(order_id,idempotency_key,canonical(p),'PROPOSED',time.time(),get(t,'cursor')))
            audit(t,'proposal',{'id':order_id,'payload':p})
    request_risk('/authorize',order_id)
    with transaction() as t: return order_view(t.one('SELECT * FROM orders WHERE id=?',(order_id,)))

@app.post('/api/orders/{order_id}/execute')
def execute(order_id:str):
    with transaction() as t:
        o=t.one('SELECT * FROM orders WHERE id=?',(order_id,))
        if not o: raise HTTPException(404,'委託不存在')
        if o['status'] in ('ACCEPTED','PARTIAL','FILLED','CANCELLED'): return order_view(o)
        if o['status']!='APPROVED' or not valid_signature(o) or o['expires']<time.time(): raise HTTPException(409,'核准失效或尚未核准')
    # Only this gateway may submit; remote consume atomically revalidates and spends approval.
    try: request_risk('/consume',order_id)
    except HTTPException as e:
        with transaction() as t:
            current=t.one('SELECT * FROM orders WHERE id=?',(order_id,))
            if current['status']=='SENT':
                t.run("UPDATE orders SET status='UNKNOWN',reason='送出回應逾時，須核對' WHERE id=?",(order_id,));audit(t,'unknown',{'id':order_id})
        raise e
    with transaction() as t:
        o=t.one('SELECT * FROM orders WHERE id=?',(order_id,));paper.accept(t,o)
        return order_view(t.one('SELECT * FROM orders WHERE id=?',(order_id,)))

@app.post('/api/orders/{order_id}/reconcile')
def reconcile_order(order_id:str):
    # Local simulator has no external broker ambiguity: durable SENT is the authoritative receipt.
    with transaction() as t:
        o=t.one('SELECT * FROM orders WHERE id=?',(order_id,))
        if not o: raise ValueError('委託不存在')
        if o['status'] in ('UNKNOWN','SENT'):
            if not valid_signature(o) or reconciliation(t): raise ValueError('核對失敗，保留未知狀態')
            t.run("UPDATE orders SET status='ACCEPTED',reason='本機 PaperBroker 持久狀態已核對' WHERE id=?",(order_id,))
            audit(t,'order_reconciled',{'id':order_id})
    return {'ok':True}

@app.post('/api/orders/{order_id}/cancel')
def cancel(order_id:str):
    with transaction() as t:
        o=t.one('SELECT * FROM orders WHERE id=?',(order_id,))
        if not o: raise ValueError('委託不存在')
        paper.cancel(t,o)
    return {'ok':True}

class Control(BaseModel): action:str; enabled:bool=True; reason:str=Field(default='',max_length=500)
@app.post('/api/control')
def control(body:Control):
    with transaction() as t:
        if body.action in ('pause_strategy','stop_risk','session_open'): put(t,body.action,body.enabled)
        elif body.action=='cancel_all':
            for o in t.all("SELECT * FROM orders WHERE status IN ('APPROVED','ACCEPTED','PARTIAL')"): paper.cancel(t,o)
        elif body.action=='unlock':
            if len(body.reason.strip())<8: raise ValueError('請填寫至少 8 字人工檢查紀錄')
            if reconciliation(t): raise ValueError('帳務仍不一致，不能解除')
            prev='0'*64
            for r in t.all('SELECT * FROM audit ORDER BY id'):
                if r['prev']!=prev or hashlib.sha256((prev+r['body']).encode()).hexdigest()!=r['hash']: raise ValueError('稽核鏈仍有差異，不能解除')
                prev=r['hash']
            for name in ('risk','watchdog','backend'):
                h=t.one('SELECT * FROM heartbeats WHERE service=?',(name,))
                if not h or time.time()-h['time']>get(t,'rules')['heartbeat_ttl']: raise ValueError('請先恢復所有服務心跳')
            if t.one("SELECT id FROM orders WHERE status IN ('UNKNOWN','SENT')"): raise ValueError('請先核對未知委託')
            if account(t)['drawdown']>=get(t,'rules')['drawdown']: raise ValueError('回撤仍超門檻，請先減倉或檢查規則')
            put(t,'locked',False)
        else: raise ValueError('未知操作；LIVE 不允許')
        audit(t,'manual_control',body.model_dump())
    return {'ok':True}

@app.post('/api/alerts/{alert_id}/ack')
def acknowledge(alert_id:int):
    with transaction() as t:
        t.run('UPDATE alerts SET acknowledged=1 WHERE id=?',(alert_id,));audit(t,'ack_alert',{'id':alert_id})
    return {'ok':True}

class Mode(BaseModel): mode:str
@app.post('/api/mode')
def mode(body:Mode): raise HTTPException(403,'LIVE 永久禁用；PAPER 無已驗證即時來源。DEMO / REPLAY 請使用資料集啟用操作。')

class Job(BaseModel):
    kind:str; dataset:str; start:str=''; end:str=''; strategy:str='trend'; fast:int=5; slow:int=20
@app.post('/api/jobs')
def create_job(body:Job):
    from research.worker import code_version
    if body.kind not in ('research','backtest') or body.strategy not in ('trend','hold'): raise ValueError('不支援的工作類型')
    with transaction() as t:
        if not t.one('SELECT id FROM datasets WHERE id=?',(body.dataset,)): raise ValueError('資料集不存在')
        p=body.model_dump();p['rules']=get(t,'rules');p['code_version']=code_version()
        key=('research:'+body.dataset+':'+code_version()) if body.kind=='research' else hashlib.sha256(canonical(p).encode()).hexdigest()
        existing=t.one('SELECT id FROM jobs WHERE key=?',(key,))
        if existing: return existing
        jid=str(uuid.uuid4());t.run('INSERT INTO jobs(id,kind,key,status,created,params) VALUES(?,?,?,?,?,?)',(jid,body.kind,key,'QUEUED',time.time(),canonical(p)));audit(t,'job_queued',{'id':jid})
        return {'id':jid}

@app.get('/api/jobs/{jid}')
def job_result(jid:str):
    with transaction() as t:
        j=t.one('SELECT * FROM jobs WHERE id=?',(jid,))
        if not j: raise HTTPException(404,'工作不存在')
        if j['result']: j['result']=json.loads(j['result'])
        return j

@app.post('/api/models/{mid}/approve')
def approve_model(mid:str):
    with transaction() as t:
        m=t.one('SELECT * FROM models WHERE id=?',(mid,))
        if not m or not json.loads(m['report'])['eligible']: raise ValueError('候選未通過預先設定的驗證門檻，不可核准')
        if get(t,'mode') not in ('DEMO','REPLAY'): raise ValueError('僅限模擬環境')
        if get(t,'virtual_date')<=model_available_after(t,m): raise ValueError('目前重播日期早於模型訓練結束日及標籤完成時間；請推進行情至訓練期之後才能核准，避免未來資訊洩漏')
        put(t,'active_model',mid);t.run('UPDATE models SET approved=1 WHERE id=?',(mid,));audit(t,'model_approved',{'id':mid})
    return {'ok':True}

@app.post('/api/models/reset')
def reset_model():
    with transaction() as t: put(t,'active_model',None);audit(t,'model_reset',{})
    return {'ok':True}

class Schedule(BaseModel): enabled:bool; interval:int=Field(ge=60,le=604800)
@app.post('/api/schedule')
def schedule(body:Schedule):
    with transaction() as t: put(t,'schedule',{**body.model_dump(),'last':0});audit(t,'schedule',body.model_dump())
    return {'ok':True}

@app.get('/api/strategy/{symbol}')
def strategy(symbol:str):
    from research.engine import trend_signal,features
    import pandas as pd, joblib
    with transaction() as t:
        if get(t,'pause_strategy'): return {'signal':'HOLD','reason':'策略已暫停'}
        q=quote(t,symbol)
        if not q: raise ValueError('標的缺少目前行情')
        rows=t.all('SELECT * FROM bars WHERE dataset=? AND idx<=? ORDER BY idx',(q['dataset'],get(t,'cursor')))
        signal=trend_signal([b['close'] for b in rows]); reason='MA5 > MA20 且當日拉回買入；MA5 < MA20 賣出；最早次日開盤成交'
        mid=get(t,'active_model')
        if mid:
            m=t.one('SELECT * FROM models WHERE id=?',(mid,))
            if m['dataset']!=q['dataset']: return {'signal':'HOLD','reason':'目前候選模型與行情資料版本不一致'}
            if len(rows)<20: return {'signal':'HOLD','reason':'模型特徵資料不足'}
            pipeline=joblib.load(m['artifact']);prediction=int(pipeline.predict(features(pd.DataFrame(rows)).iloc[[-1]])[0])
            signal='BUY' if prediction else 'SELL';reason='人工核准邏輯迴歸候選：5 日方向分類（非勝率）'
        a=account(t);held=next((p['quantity'] for p in a['positions'] if p['symbol']==symbol),0)
        if signal=='BUY' and held: signal='HOLD';reason+='；已有部位，不攤平加碼'
        if signal=='SELL' and not held: signal='HOLD'
        price=round_tick(q['close']*(1.015 if signal=='BUY' else .985),get(t,'rules')['whitelist'][symbol],up=signal=='BUY')
        qty=min(999,int(a['nav']*get(t,'rules')['per_order']/price)) if signal=='BUY' else min(999,held)
        return {'signal':signal,'reason':reason,'quantity':qty,'price':price,'unit':'ODD','symbol':symbol,'model_id':mid,'estimated_fee':max(get(t,'rules')['min_fee'],qty*price*get(t,'rules')['fee_rate'])}

class RulesPatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    per_order:float=Field(gt=0,le=.05);per_symbol:float=Field(gt=0,le=.15);total:float=Field(gt=0,le=.5)
    daily_loss:float=Field(gt=0,le=.01);drawdown:float=Field(gt=0,le=.05)
    daily_proposals:int=Field(ge=1,le=100);fee_rate:float=Field(ge=0,le=.01);min_fee:float=Field(ge=0,le=100)
    slippage:float=Field(ge=0,le=.02);participation:float=Field(gt=0,le=.1)
@app.post('/api/rules')
def rules(body:RulesPatch):
    with transaction() as t:
        if t.one("SELECT id FROM orders WHERE status IN ('APPROVED','SENT','ACCEPTED','PARTIAL','UNKNOWN')"): raise ValueError('請先撤銷或核對未完成委託後修改規則')
        r=get(t,'rules');r.update(body.model_dump());r['version']+=1
        if not r['per_order']<=r['per_symbol']<=r['total']: raise ValueError('單筆 ≤ 單一標的 ≤ 總曝險')
        put(t,'rules',r);t.run('INSERT INTO rule_versions VALUES(?,?,?)',(r['version'],time.time(),canonical(r)));audit(t,'rules_changed',r)
    return {'ok':True,'version':r['version']}

if (ROOT/'frontend/dist').exists(): app.mount('/',StaticFiles(directory=ROOT/'frontend/dist',html=True),name='frontend')
