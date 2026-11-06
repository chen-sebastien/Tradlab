import asyncio, time, uuid, json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from backend.db import transaction, get, audit, heartbeat, alert
from backend.domain import evaluate, Rejected, reserve, signed, valid_signature, transition, expire
from backend.security import install_security

async def pulse():
    instance=str(uuid.uuid4())
    while True:
        with transaction() as t:
            heartbeat(t,'risk',instance); expire(t)
        await asyncio.sleep(2)

@asynccontextmanager
async def life(app):
    task=asyncio.create_task(pulse())
    yield
    task.cancel()

app=FastAPI(title='TradingLab 獨立風控',lifespan=life)
install_security(app,'service')
class Request(BaseModel): id: str
@app.get('/health')
def health(): return {'service':'risk','ok':True}

@app.post('/authorize')
def authorize(req:Request):
    with transaction() as t:
        expire(t)
        o=t.one('SELECT * FROM orders WHERE id=?',(req.id,))
        if not o: raise HTTPException(404,'未知提案')
        if o['status']!='PROPOSED': return o
        p=json.loads(o['payload'])
        try:
            rules=evaluate(t,p)
            expiry=time.time()+rules['approval_ttl']
            token=signed(o['id'],p,expiry,rules['version'])
            transition(t,o,'APPROVED')
            t.run('UPDATE orders SET reserved=?,approval=?,expires=?,rule_version=?,reason=? WHERE id=?',
                (reserve(p,p['quantity'],rules) if p['side']=='BUY' else 0,token,expiry,rules['version'],'OK：獨立風控核准',o['id']))
        except Rejected as e:
            transition(t,o,'RISK_REJECTED')
            t.run('UPDATE orders SET reason=?,rule_version=? WHERE id=?',(e.code+'：'+e.message,get(t,'rules')['version'],o['id']))
            if e.code in ('ACCOUNT_MISMATCH','DRAWDOWN'): alert(t,e.code,{'order':o['id']})
        return t.one('SELECT * FROM orders WHERE id=?',(o['id'],))

@app.post('/consume')
def consume(req:Request):
    error=None
    with transaction() as t:
        o=t.one('SELECT * FROM orders WHERE id=?',(req.id,))
        if not o: raise HTTPException(404,'未知委託')
        if o['status'] in ('SENT','ACCEPTED','PARTIAL','FILLED'): return {'consumed':True,'idempotent':True}
        try:
            if o['status']!='APPROVED' or not valid_signature(o) or o['expires']<time.time():
                raise Rejected('APPROVAL','核准失效、已使用或內容被修改')
            if o['rule_version']!=get(t,'rules')['version']: raise Rejected('RULE_VERSION','規則已變更，請重新提案')
            evaluate(t,json.loads(o['payload']),exclude=o['id'],check_quota=False)
            transition(t,o,'SENT')
        except Rejected as e:
            error=e.message
            if o['status']=='APPROVED':
                transition(t,o,'FAILED')
                t.run('UPDATE orders SET reserved=0,reason=? WHERE id=?',(e.code+'：'+e.message,o['id']))
            if e.code in ('DRAWDOWN','ACCOUNT_MISMATCH'): alert(t,e.code,{'order':o['id']})
    if error: raise HTTPException(409,error)
    return {'consumed':True,'idempotent':False}
