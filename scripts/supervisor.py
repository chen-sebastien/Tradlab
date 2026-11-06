import subprocess, sys, time, json, os, socket
from backend.config import ROOT,LOCAL

def main():
    for port in (8765,8766):
        with socket.socket() as s:
            try: s.bind(('127.0.0.1',port))
            except OSError: raise RuntimeError(f'Port {port} 已被使用，拒絕啟動重複程序')
    record=LOCAL/'supervisor.json'
    with record.open('x') as f: json.dump({'pid':os.getpid(),'root':str(ROOT),'started':time.time(),'children':[]},f)
    children=[];logs=[]
    try:
        for name,args in [('risk',['uvicorn','risk_service.app:app','--host','127.0.0.1','--port','8766']),
            ('backend',['uvicorn','backend.app:app','--host','127.0.0.1','--port','8765']),
            ('watchdog',['watchdog.main']),('research',['research.worker'])]:
            log=(LOCAL/(name+'.log')).open('a',encoding='utf-8');logs.append(log)
            child=subprocess.Popen([sys.executable,'-m',*args],cwd=ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            children.append((name,child));time.sleep(1)
        record.write_text(json.dumps({'pid':os.getpid(),'root':str(ROOT),'started':time.time(),'children':[{'service':n,'pid':p.pid} for n,p in children]}))
        while not (LOCAL/'stop').exists():
            # Do not automatically restart crashed safety services or silently clear locks.
            for name,p in children:
                if p.poll() is not None: print(f'{name} exited: {p.returncode}',flush=True)
            time.sleep(2)
    finally:
        for name,p in reversed(children):
            if p.poll() is None: p.terminate()
        for name,p in children:
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill();p.wait()
        for log in logs: log.close()
        record.unlink(missing_ok=True);(LOCAL/'stop').unlink(missing_ok=True)

if __name__=='__main__': main()
