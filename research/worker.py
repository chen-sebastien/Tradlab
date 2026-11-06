import time, json, uuid, traceback, hashlib
from pathlib import Path
from backend.db import transaction, get, put, heartbeat, audit, canonical
from backend.config import ROOT
from research.engine import backtest,train

def code_version():
    return hashlib.sha256((ROOT/'research/engine.py').read_bytes()).hexdigest()

def run_one():
    with transaction() as t:
        job=t.one("SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created LIMIT 1")
        if not job: return False
        t.run("UPDATE jobs SET status='RUNNING',progress=10 WHERE id=?",(job['id'],))
        p=json.loads(job['params']); dataset=t.one('SELECT * FROM datasets WHERE id=?',(p['dataset'],))
        rows=t.all('SELECT * FROM bars WHERE dataset=? ORDER BY idx',(p['dataset'],)); rules=p['rules']
    try:
        if job['kind']=='backtest':
            rows=[r for r in rows if (not p.get('start') or r['date']>=p['start']) and (not p.get('end') or r['date']<=p['end'])]
            result=backtest(rows,rules,rules['whitelist'][dataset['symbol']],p.get('strategy','trend'),p.get('fast',5),p.get('slow',20))
        else:
            if len(rows)<250: result={'skipped':True,'reason':'資料不足 250 筆'}
            else:
                with transaction() as t:
                    # Dataset hash is immutable. Repeat experiments cannot consume holdout again.
                    if t.one('SELECT key FROM holdouts WHERE key=?',(dataset['hash'],)):
                        result={'skipped':True,'reason':'此資料版本保留測試集已使用；重複工作不再評估'}
                    else:
                        t.run('INSERT INTO holdouts VALUES(?,?,?)',(dataset['hash'],time.time(),job['id'])); result=None
                if result is None: result=train(rows,dataset['hash'],job['id'],code_version())
        with transaction() as t:
            if job['kind']=='research' and not result.get('skipped'):
                t.run('INSERT INTO models(id,job_id,dataset,report,artifact) VALUES(?,?,?,?,?)',
                    (result['id'],job['id'],p['dataset'],canonical(result),result['artifact']))
            t.run("UPDATE jobs SET status=?,progress=100,result=? WHERE id=?",('SKIPPED' if result.get('skipped') else 'COMPLETED',canonical(result),job['id']))
            audit(t,'job_complete',{'id':job['id'],'kind':job['kind']})
    except Exception as e:
        with transaction() as t:
            t.run("UPDATE jobs SET status='FAILED',error=?,progress=100 WHERE id=?",(str(e)[:1000],job['id']))
            audit(t,'job_failed',{'id':job['id'],'error':str(e)[:500]})
    return True

def schedule(t):
    cfg=get(t,'schedule',{'enabled':False,'interval':3600,'last':0})
    if not cfg['enabled'] or time.time()-cfg['last']<cfg['interval']: return
    for value in get(t,'active',{}).values():
        ds=value['dataset']; key='research:'+ds+':'+code_version()
        if not t.one('SELECT id FROM jobs WHERE key=?',(key,)):
            t.run('INSERT INTO jobs(id,kind,key,status,created,params) VALUES(?,?,?,?,?,?)',
                (str(uuid.uuid4()),'research',key,'QUEUED',time.time(),canonical({'dataset':ds,'rules':get(t,'rules')})))
    cfg['last']=time.time(); put(t,'schedule',cfg)

def main():
    instance=str(uuid.uuid4())
    with transaction() as t:
        t.run("UPDATE jobs SET status='FAILED',error='worker 重啟；保留原結果，不自動重試已消耗測試集的工作' WHERE status='RUNNING'")
    while True:
        with transaction() as t:
            heartbeat(t,'research',instance); schedule(t)
        if not run_one(): time.sleep(2)

if __name__=='__main__': main()
