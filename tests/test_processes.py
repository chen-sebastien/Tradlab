"""Real HTTP and independent process failure acceptance, isolated temporary database."""
import os,sys,time,subprocess,uuid,json
import httpx
from backend.config import ROOT,DATA,credentials
from backend.db import transaction,get,put

def wait_for(fn,timeout=25):
    end=time.time()+timeout
    while time.time()<end:
        try:
            value=fn()
            if value:return value
        except (httpx.HTTPError,KeyError,AssertionError):pass
        time.sleep(.2)
    raise AssertionError('Timed out waiting for independent process')

def test_real_services_restart_and_failure_modes():
    env={**os.environ,'TRADINGLAB_RISK_URL':'http://127.0.0.1:8866','PYTHONUTF8':'1'}
    with transaction() as t:
        r=get(t,'rules');r['heartbeat_ttl']=3;put(t,'rules',r)
    processes={};logs=[]
    def start(name,module,port=None):
        log=(DATA/(name+'.test.log')).open('a',encoding='utf-8');logs.append(log)
        args=['uvicorn',module,'--host','127.0.0.1','--port',str(port)] if port else [module]
        processes[name]=subprocess.Popen([sys.executable,'-m',*args],cwd=ROOT,env=env,stdout=log,stderr=log)
    client=httpx.Client(base_url='http://127.0.0.1:8865',headers={'Authorization':'Bearer '+credentials()['client']},timeout=8)
    def state():return client.get('/api/state').json()
    def post(path,data=None,headers=None):return client.post(path,json=data or {},headers=headers)
    try:
        start('risk','risk_service.app:app',8866);start('backend','backend.app:app',8865)
        wait_for(lambda:client.get('/health').status_code==200)
        start('watchdog','watchdog.main');start('research','research.worker')
        wait_for(lambda:all(state()['server_time']-h['time']<3 for h in state()['heartbeats']))
        s=state();assert not s['locked'];q=s['quotes'][0]
        payload={'symbol':q['symbol'],'side':'BUY','quantity':90,'price':48.5,'unit':'ODD','reason':'HTTP process test'}
        key=str(uuid.uuid4());response=post('/api/proposals',payload,{'Idempotency-Key':key});assert response.status_code==200,response.text
        o=response.json();assert o['status']=='APPROVED',o
        duplicate=post('/api/proposals',payload,{'Idempotency-Key':key}).json();assert duplicate['id']==o['id']
        assert post('/api/orders/'+o['id']+'/execute').status_code==200
        assert post('/api/orders/'+o['id']+'/execute').status_code==200
        assert post('/api/replay/step').status_code==200
        s=state();assert s['orders'][0]['status']=='PARTIAL';assert len(s['fills'])==1
        before=s['account']['cash']
        processes['backend'].terminate();processes['backend'].wait(10)
        start('backend','backend.app:app',8865);wait_for(lambda:client.get('/health').status_code==200)
        assert post('/api/orders/'+o['id']+'/execute').status_code==200
        assert state()['account']['cash']==before
        assert post('/api/orders/'+o['id']+'/cancel').status_code==200;assert state()['account']['reserved']==0
        # Import and validate actual HTTP data, without replacing live simulation state.
        csv='date,open,high,low,close,volume\n2024-01-02,40,42,39,41,2000\n2024-01-03,41,43,40,42,3000'
        assert post('/api/data/import',{'symbol':'0050','csv':csv,'source':'integration fixture'}).status_code==200
        ds=s['active']['0050']['dataset']
        for kind in ('backtest','research'):
            job=post('/api/jobs',{'kind':kind,'dataset':ds}).json()['id']
            done=wait_for(lambda:(r if (r:=client.get('/api/jobs/'+job).json())['status'] in ('COMPLETED','FAILED') else None),40)
            assert done['status']=='COMPLETED',done
            assert post('/api/jobs',{'kind':kind,'dataset':ds}).json()['id']==job
        assert state()['models'];assert state()['active_model'] is None
        assert post('/api/mode',{'mode':'LIVE'}).status_code==403
        # Actual stopped service, not merely a mocked response.
        processes['risk'].terminate();processes['risk'].wait(10)
        assert post('/api/proposals',payload,{'Idempotency-Key':str(uuid.uuid4())}).status_code==503
        start('risk','risk_service.app:app',8866)
        wait_for(lambda:httpx.get('http://127.0.0.1:8866/health').status_code==200)
        processes['watchdog'].terminate();processes['watchdog'].wait(10);time.sleep(3.3)
        blocked=post('/api/proposals',payload,{'Idempotency-Key':str(uuid.uuid4())}).json()
        assert blocked['status']=='RISK_REJECTED';assert 'HEARTBEAT_WATCHDOG' in blocked['reason']
        # A durable account mismatch must lock across watchdog and backend restarts.
        with transaction() as t:put(t,'cash',get(t,'cash')+1)
        start('watchdog','watchdog.main');wait_for(lambda:state()['locked'])
        processes['backend'].terminate();processes['backend'].wait(10)
        start('backend','backend.app:app',8865);wait_for(lambda:client.get('/health').status_code==200)
        assert state()['locked'];assert any(a['code']=='RECONCILIATION' for a in state()['alerts'])
    finally:
        client.close()
        for p in processes.values():
            if p.poll() is None:p.terminate()
            p.wait(10)
        for log in logs:log.close()
