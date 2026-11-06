import os,tempfile
from pathlib import Path
TEST_ROOT=Path(tempfile.mkdtemp(prefix='tradinglab-tests-'))
os.environ['TRADINGLAB_DATA']=str(TEST_ROOT/'data')
os.environ['TRADINGLAB_LOCAL']=str(TEST_ROOT/'local')
import pytest
from alembic.config import Config
from alembic import command
from backend.config import ROOT
from backend.db import transaction,engine,heartbeat
from scripts.init_db import initialize

@pytest.fixture(autouse=True)
def fresh():
    with engine.connect() as c:
        c.exec_driver_sql('PRAGMA foreign_keys=OFF')
        names=[r[0] for r in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in names: c.exec_driver_sql('DROP TABLE "'+name+'"')
        c.commit();c.exec_driver_sql('PRAGMA foreign_keys=ON')
    config=Config(str(ROOT/'alembic.ini'));config.set_main_option('script_location',str(ROOT/'migrations'));command.upgrade(config,'head');initialize()
    with transaction() as t:
        for service in ('backend','risk','watchdog','research'): heartbeat(t,service,'test-instance')
    yield
