import csv
from backend.db import transaction,get,put,audit,canonical
from backend.config import DEFAULT_RULES,DATA,credentials
from backend.market import demo_rows,save_dataset

def initialize():
    credentials()
    with transaction() as t:
        if get(t,'initialized'): return
        for key,value in dict(rules=DEFAULT_RULES,cash=100000.,peak=100000.,day_start=100000.,locked=False,
             stop_risk=False,pause_strategy=False,mode='DEMO',cursor=60,session_open=True,active_model=None,
             schedule={'enabled':False,'interval':3600,'last':0}).items(): put(t,key,value)
        t.run('INSERT INTO ledger(delta,reason) VALUES(?,?)',(100000,'初始模擬資金'))
        t.run('INSERT INTO rule_versions VALUES(?,?,?)',(1,0,canonical(DEFAULT_RULES)))
        active={}
        for symbol,seed in [('0050',42),('006208',43),('2330',44)]:
            rows=demo_rows(seed)
            ds=save_dataset(t,symbol,rows,f'DEMO 合成日線 seed={seed}','DEMO')
            active[symbol]={'dataset':ds}
            with (DATA/f'demo-{symbol}.csv').open('w',newline='',encoding='utf-8') as f:
                writer=csv.DictWriter(f,fieldnames=['date','open','high','low','close','volume']);writer.writeheader();writer.writerows(rows)
        put(t,'active',active);put(t,'virtual_date',rows[60]['date']);put(t,'initialized',True)
        audit(t,'initialize',{'capital':100000,'mode':'DEMO'})

if __name__=='__main__': initialize()
