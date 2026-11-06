import time, uuid, hashlib, json
from backend.db import transaction, get, put, heartbeat, alert
from backend.domain import reconciliation, account, expire
from backend.market import quote, active

def inspect(t, check_beats=True):
    expire(t)
    errors=reconciliation(t)
    if errors: alert(t,'RECONCILIATION',errors)
    prev='0'*64
    for row in t.all('SELECT * FROM audit ORDER BY id'):
        expected=hashlib.sha256((prev+row['body']).encode()).hexdigest()
        if row['prev']!=prev or row['hash']!=expected:
            alert(t,'AUDIT_CHAIN',{'id':row['id']}); break
        prev=row['hash']
    rules=get(t,'rules')
    if check_beats:
        for service in ('backend','risk'):
            b=t.one('SELECT * FROM heartbeats WHERE service=?',(service,))
            if not b or time.time()-b['time']>rules['heartbeat_ttl']: alert(t,'SERVICE_'+service.upper(),b or {'missing':service})
    a=account(t)
    if a['drawdown']>=rules['drawdown']: alert(t,'DRAWDOWN',{'drawdown':a['drawdown']})
    for symbol in active(t):
        q=quote(t,symbol)
        if not q or q['date']!=get(t,'virtual_date'): alert(t,'QUOTE_'+symbol,{'symbol':symbol,'virtual_date':get(t,'virtual_date')})
    unknown=t.all("SELECT id,status FROM orders WHERE status IN ('UNKNOWN','SENT') AND created<?",(time.time()-30,))
    if unknown: alert(t,'UNKNOWN_ORDERS',unknown)
    recent=t.all('SELECT status FROM orders WHERE created>?',(time.time()-60,))
    rejected=sum(x['status']=='RISK_REJECTED' for x in recent)
    if rejected>=5: alert(t,'REJECTION_SPIKE',{'rejected_last_60s':rejected},False)
    if len(recent)>rules['daily_proposals']*2: alert(t,'ORDER_FREQUENCY',{'last_60s':len(recent)})
    duplicate=t.all('SELECT order_id,bar,COUNT(*) n FROM fills GROUP BY order_id,bar HAVING COUNT(*)>1')
    if duplicate: alert(t,'DUPLICATE_FILL',duplicate)
    proposals=t.all("SELECT payload,COUNT(*) n FROM orders WHERE created>? AND status NOT IN ('RISK_REJECTED','FAILED') GROUP BY payload HAVING COUNT(*)>=3",(time.time()-3,))
    if proposals: alert(t,'DUPLICATE_PROPOSALS',proposals)
    return errors

def main():
    instance=str(uuid.uuid4())
    while True:
        try:
            with transaction() as t:
                inspect(t); heartbeat(t,'watchdog',instance)
        except Exception as e:
            # Do not update heartbeat on failure; gateway fails closed.
            print('watchdog failure:',type(e).__name__,flush=True)
        time.sleep(2)

if __name__=='__main__': main()
