import json, time, math, hashlib, hmac
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from backend.config import credentials
from backend.db import get, put, canonical, audit, alert
from backend.market import quote

OPEN = ('APPROVED','SENT','ACCEPTED','PARTIAL','UNKNOWN')

class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    symbol: str = Field(pattern=r'^\d{4,6}$')
    side: Literal['BUY','SELL']
    quantity: int = Field(gt=0,le=1000000,strict=True)
    price: float = Field(gt=0,le=1000000)
    unit: Literal['ODD','BOARD'] = 'ODD'
    reason: str = Field(default='人工研究提案',max_length=200)
    model_id: str | None = Field(default=None,max_length=40)

class Rejected(Exception):
    def __init__(self, code, message): self.code,self.message=code,message; super().__init__(message)

def reject(code,message): raise Rejected(code,message)
def tick(price, kind):
    if kind=='ETF': return .01 if price<50 else .05
    return .01 if price<10 else .05 if price<50 else .1 if price<100 else .5 if price<500 else 1 if price<1000 else 5

def round_tick(price, kind, up=False):
    step=tick(price,kind)
    return round((math.ceil(price/step-1e-9) if up else math.floor(price/step+1e-9))*step,2)

def costs(value, side, kind, rules):
    return round(max(rules['min_fee'],value*rules['fee_rate']),2), round(value*(rules['etf_tax'] if kind=='ETF' else rules['stock_tax']),2) if side=='SELL' else 0

def account(t):
    cash=get(t,'cash',100000.0)
    positions=t.all('SELECT * FROM positions WHERE quantity>0')
    exposure=0
    for p in positions:
        q=quote(t,p['symbol']); p['price']=q['close'] if q else None
        p['value']=p['quantity']*(q['close'] if q else p['cost'])
        p['unrealized']=p['value']-p['quantity']*p['cost']; exposure+=p['value']
    reserved=t.one("SELECT COALESCE(SUM(reserved),0) AS v FROM orders WHERE status IN ('APPROVED','SENT','ACCEPTED','PARTIAL','UNKNOWN')")['v']
    nav=cash+exposure; peak=get(t,'peak',100000)
    return dict(cash=round(cash,2),available=round(cash-reserved,2),reserved=round(reserved,2),positions=positions,exposure=exposure,nav=round(nav,2),drawdown=max(0,1-nav/peak),peak=peak,day_start=get(t,'day_start',100000),settlement='簡化為即時交割，非實際 T+2；可用現金已扣保留款')

def expire(t):
    expired=t.all("SELECT id FROM orders WHERE status='APPROVED' AND expires<?",(time.time(),))
    for row in expired:
        t.run("UPDATE orders SET status='FAILED',reserved=0,reason='核准逾時' WHERE id=?",(row['id'],))
        audit(t,'approval_expired',row)

def reconciliation(t):
    expected=t.one('SELECT COALESCE(SUM(delta),0) v FROM ledger')['v']
    errors=[]
    if abs(get(t,'cash',0)-expected)>.011: errors.append({'cash':get(t,'cash'),'ledger':expected})
    computed={}
    for f in t.all('SELECT f.quantity,o.payload FROM fills f JOIN orders o ON f.order_id=o.id'):
        p=json.loads(f['payload']); computed[p['symbol']]=computed.get(p['symbol'],0)+f['quantity']*(1 if p['side']=='BUY' else -1)
    actual={p['symbol']:p['quantity'] for p in t.all('SELECT * FROM positions') if p['quantity']}
    if {k:v for k,v in computed.items() if v}!=actual: errors.append({'positions':actual,'fills':computed})
    for o in t.all('SELECT * FROM orders'):
        fills=t.one('SELECT COALESCE(SUM(quantity),0) v FROM fills WHERE order_id=?',(o['id'],))['v']
        if fills!=o['filled']: errors.append({'order':o['id'],'filled':o['filled'],'ledger_fills':fills})
        p=json.loads(o['payload']); r=get(t,'rules')
        remaining=p['quantity']-o['filled']
        expected_res=reserve(p,remaining,r) if o['status'] in OPEN and p['side']=='BUY' else 0
        if abs(o['reserved']-expected_res)>.011: errors.append({'order':o['id'],'reserved':o['reserved'],'expected':expected_res})
    return errors

def reserve(p, remaining, rules):
    return round(p['price']*remaining+max(rules['min_fee'],p['price']*remaining*rules['fee_rate']),2) if remaining else 0

def evaluate(t,p,exclude=None,check_quota=True):
    rules=get(t,'rules'); a=account(t)
    if get(t,'mode') not in ('DEMO','REPLAY'): reject('MODE_DISABLED','僅允許 DEMO / REPLAY')
    if p['symbol'] not in rules['whitelist']: reject('SYMBOL','標的不在白名單')
    if p['unit']=='BOARD' and p['quantity']%1000: reject('UNIT','整股數量必須為 1,000 股的整數倍')
    if p['unit']=='ODD' and p['quantity']>=1000: reject('UNIT','零股數量必須介於 1–999 股')
    step=Decimal(str(tick(p['price'],rules['whitelist'][p['symbol']])))
    if Decimal(str(p['price'])) % step != 0: reject('TICK','價格不符合升降單位')
    q=quote(t,p['symbol'])
    if not q: reject('MISSING_QUOTE','缺少目前虛擬交易日行情')
    if q['metadata']['adjusted']: reject('ADJUSTED_PRICE','還原價格不能用於模擬委託')
    if get(t,'session_open',True) is not True: reject('MARKET_CLOSED','重播交易時段已關閉')
    # Virtual freshness compares source date to explicit replay clock, never wall time.
    if q['date']!=get(t,'virtual_date'): reject('STALE_QUOTE','行情與虛擬交易時鐘不一致')
    if not q['low']*.9 <= p['price'] <= q['high']*1.1: reject('PRICE_BAND','限價偏離研究行情保護區間')
    errors=reconciliation(t)
    if errors: reject('ACCOUNT_MISMATCH','帳務不一致，禁止送單')
    if any(x['price'] is None for x in a['positions']): reject('MISSING_VALUATION','持倉缺少估值行情')
    for service in ('risk','watchdog'):
        beat=t.one('SELECT * FROM heartbeats WHERE service=?',(service,))
        if not beat or time.time()-beat['time']>rules['heartbeat_ttl']: reject('HEARTBEAT_'+service.upper(),service+' 心跳過期')
    orders=[o for o in t.all("SELECT * FROM orders WHERE status IN ('APPROVED','SENT','ACCEPTED','PARTIAL','UNKNOWN')") if o['id']!=exclude]
    if p['side']=='SELL':
        held=next((x['quantity'] for x in a['positions'] if x['symbol']==p['symbol']),0)
        pending=sum(json.loads(o['payload'])['quantity']-o['filled'] for o in orders if json.loads(o['payload'])['symbol']==p['symbol'] and json.loads(o['payload'])['side']=='SELL')
        if p['quantity']>held-pending: reject('OVERSELL','超過實際可售持股（含未成交賣單）')
        return rules
    if get(t,'locked') or get(t,'stop_risk'): reject('LOCKED','新增風險已鎖定／暫停')
    if a['nav']<=a['day_start']*(1-rules['daily_loss']): reject('DAILY_LOSS','達單日損失門檻')
    if a['drawdown']>=rules['drawdown']: reject('DRAWDOWN','達最大回撤門檻，需人工檢查')
    value=p['price']*p['quantity']
    if value>a['nav']*rules['per_order']+.001: reject('PER_ORDER','單筆買入超過淨值比例')
    buy=[(o,json.loads(o['payload'])) for o in orders if json.loads(o['payload'])['side']=='BUY']
    reserves=sum(o['reserved'] for o,_ in buy)
    if reserve(p,p['quantity'],rules)>a['cash']-reserves+.001: reject('CASH','可用資金不足')
    pending=sum(x['price']*(x['quantity']-o['filled']) for o,x in buy)
    symbol_pending=sum(x['price']*(x['quantity']-o['filled']) for o,x in buy if x['symbol']==p['symbol'])
    symbol_value=sum(x['value'] for x in a['positions'] if x['symbol']==p['symbol'])
    if value+symbol_pending+symbol_value>a['nav']*rules['per_symbol']+.001: reject('SYMBOL_EXPOSURE','單一標的總曝險超標（包含保留委託）')
    if value+pending+a['exposure']>a['nav']*rules['total']+.001: reject('TOTAL_EXPOSURE','總曝險超標（包含保留委託）')
    if check_quota:
        count=t.one("SELECT COUNT(*) n FROM orders WHERE bar=? AND json_extract(payload,'$.side')='BUY'",(get(t,'cursor'),))['n']
        if count>rules['daily_proposals']: reject('QUOTA','每日新風險提案數量已達上限')
    return rules

def signed(order_id,payload,expires,version):
    body=canonical({'id':order_id,'payload':payload,'expires':expires,'version':version})
    return hmac.new(credentials()['signing'].encode(),body.encode(),hashlib.sha256).hexdigest()

def valid_signature(order):
    expected=signed(order['id'],json.loads(order['payload']),order['expires'],order['rule_version'])
    return hmac.compare_digest(expected,order['approval'] or '')

TRANSITIONS={'PROPOSED':{'APPROVED','RISK_REJECTED'},'APPROVED':{'SENT','CANCELLED','FAILED'},
 'SENT':{'ACCEPTED','UNKNOWN','FAILED'},'UNKNOWN':{'ACCEPTED','FAILED'},
 'ACCEPTED':{'PARTIAL','FILLED','CANCELLED','FAILED'},'PARTIAL':{'PARTIAL','FILLED','CANCELLED','FAILED'}}
def transition(t,order,new):
    if new not in TRANSITIONS.get(order['status'],set()): raise ValueError(f"非法狀態轉移 {order['status']} → {new}")
    t.run('UPDATE orders SET status=? WHERE id=?',(new,order['id']))
    audit(t,'order_state',{'id':order['id'],'from':order['status'],'to':new})
