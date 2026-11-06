"""Isolated browser acceptance environment; never uses the everyday account."""
import tempfile,os,subprocess,sys,time,json
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1]
    temporary=Path(tempfile.mkdtemp(prefix='tradinglab-e2e-'))
    os.environ.update(TRADINGLAB_DATA=str(temporary/'data'),TRADINGLAB_LOCAL=str(temporary/'local'),
        TRADINGLAB_RISK_URL='http://127.0.0.1:8876',TRADINGLAB_UI_ORIGIN='http://127.0.0.1:8875',PYTHONUTF8='1')
    from alembic.config import Config
    from alembic import command
    command.upgrade(Config(str(root/'alembic.ini')),'head')
    from scripts.init_db import initialize
    from backend.config import credentials
    initialize()
    (root/'.local').mkdir(exist_ok=True)
    (root/'.local/e2e-client-token.txt').write_text(credentials()['client'])
    children=[]
    try:
        for args in [('uvicorn','risk_service.app:app','--host','127.0.0.1','--port','8876'),
            ('uvicorn','backend.app:app','--host','127.0.0.1','--port','8875'),('watchdog.main',),('research.worker',)]:
            children.append(subprocess.Popen([sys.executable,'-m',*args],cwd=root,env=os.environ.copy()))
            time.sleep(1)
        while all(c.poll() is None for c in children):time.sleep(1)
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:child.wait(10)

if __name__=='__main__':main()
