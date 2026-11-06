from abc import ABC, abstractmethod
import json, time
from backend.db import get, put, audit
from backend.domain import transition, reserve, costs, round_tick, account, reconciliation
from backend.market import quote

class BrokerAdapter(ABC):
    @abstractmethod
    def accept(self,t,order): ...
    @abstractmethod
    def match(self,t): ...
    @abstractmethod
    def cancel(self,t,order): ...

class LiveBroker(BrokerAdapter):
    def __init__(self): raise RuntimeError('LIVE 未實作且永久停用於第一版')
    def accept(self,t,order): raise RuntimeError('LIVE disabled')
    def match(self,t): raise RuntimeError('LIVE disabled')
    def cancel(self,t,order): raise RuntimeError('LIVE disabled')

class PaperBroker(BrokerAdapter):
    def accept(self,t,o):
        if o['status']=='SENT': transition(t,o,'ACCEPTED')
        elif o['status'] not in ('ACCEPTED','PARTIAL','FILLED','CANCELLED'): raise ValueError('必須先經 gateway 核准')
    def cancel(self,t,o):
        if o['status'] in ('APPROVED','ACCEPTED','PARTIAL'):
            transition(t,o,'CANCELLED'); t.run('UPDATE orders SET reserved=0 WHERE id=?',(o['id'],))
        elif o['status'] in ('SENT','UNKNOWN'): raise ValueError('狀態未知，先核對後撤單')
    def match(self,t, only_sells=False):
        rules=get(t,'rules'); cursor=get(t,'cursor')
        if reconciliation(t): raise ValueError('帳務不一致，禁止模擬成交')
        used={}
        for o in t.all("SELECT * FROM orders WHERE status IN ('ACCEPTED','PARTIAL') ORDER BY created,id"):
            if cursor>o['bar']+1:
                self.cancel(t,o)
                t.run('UPDATE orders SET reason=? WHERE id=?',('次日模擬 DAY 委託到期，取消剩餘數量',o['id']))
                continue
            p=json.loads(o['payload']); q=quote(t,p['symbol'])
            if only_sells and p['side']=='BUY': continue
            if p['side']=='BUY':
                a=account(t)
                if a['nav']<=a['day_start']*(1-rules['daily_loss']) or a['drawdown']>=rules['drawdown']: continue
                pending=[(r,json.loads(r['payload'])) for r in t.all("SELECT * FROM orders WHERE status IN ('APPROVED','SENT','ACCEPTED','PARTIAL','UNKNOWN')") if json.loads(r['payload'])['side']=='BUY']
                total_pending=sum(v['price']*(v['quantity']-r['filled']) for r,v in pending)
                symbol_pending=sum(v['price']*(v['quantity']-r['filled']) for r,v in pending if v['symbol']==p['symbol'])
                held=sum(v['value'] for v in a['positions'] if v['symbol']==p['symbol'])
                if a['exposure']+total_pending>a['nav']*rules['total'] or held+symbol_pending>a['nav']*rules['per_symbol']: continue
            if not q or cursor<=o['bar'] or t.one('SELECT id FROM fills WHERE order_id=? AND bar=?',(o['id'],cursor)): continue
            kind=rules['whitelist'][p['symbol']]
            price=round_tick(q['open']*(1+rules['slippage']*(1 if p['side']=='BUY' else -1)),kind,up=p['side']=='BUY')
            # Opening-only execution: if limit cannot cross next open, remains unfilled.
            if p['side']=='BUY' and price>p['price'] or p['side']=='SELL' and price<p['price']: continue
            capacity=max(0,int(q['volume']*rules['participation'])-used.get(p['symbol'],0))
            qty=min(p['quantity']-o['filled'],capacity)
            if p['unit']=='BOARD': qty=qty//1000*1000
            if qty<=0: continue
            position=t.one('SELECT * FROM positions WHERE symbol=?',(p['symbol'],)) or {'quantity':0,'cost':0}
            if p['side']=='SELL': qty=min(qty,position['quantity'])
            if qty<=0: continue
            value=qty*price
            previous_value=t.one('SELECT COALESCE(SUM(quantity*price),0) v FROM fills WHERE order_id=?',(o['id'],))['v']
            total_fee,_=costs(previous_value+value,p['side'],kind,rules)
            fee=round(total_fee-o['fee'],2)
            _,tax=costs(value,p['side'],kind,rules)
            delta=round(-value-fee if p['side']=='BUY' else value-fee-tax,2)
            cash=get(t,'cash')
            if cash+delta<-.001: raise ValueError('成交資金不足')
            result=t.run('INSERT INTO fills(order_id,bar,quantity,price,fee,tax,time) VALUES(?,?,?,?,?,?,?)',(o['id'],cursor,qty,price,fee,tax,time.time()))
            t.run('INSERT INTO ledger(fill_id,delta,reason) VALUES(?,?,?)',(result.lastrowid,delta,'模擬成交'))
            put(t,'cash',round(cash+delta,2))
            old=position['quantity']; new=old+qty*(1 if p['side']=='BUY' else -1)
            cost=(old*position['cost']+value+fee)/new if p['side']=='BUY' else position['cost'] if new else 0
            t.run('INSERT INTO positions VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity,cost=excluded.cost',(p['symbol'],new,cost))
            filled=o['filled']+qty
            transition(t,o,'FILLED' if filled==p['quantity'] else 'PARTIAL')
            t.run('UPDATE orders SET filled=?,fee=?,reserved=? WHERE id=?',(filled,total_fee,reserve(p,p['quantity']-filled,rules) if p['side']=='BUY' else 0,o['id']))
            used[p['symbol']]=used.get(p['symbol'],0)+qty
            audit(t,'paper_fill',{'order':o['id'],'bar':cursor,'quantity':qty,'price':price,'delta':delta})

paper=PaperBroker()
