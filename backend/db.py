import json, time, hashlib
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from backend.config import DB

engine = create_engine('sqlite:///' + DB.as_posix(), connect_args={'check_same_thread': False, 'timeout': 20})
@event.listens_for(engine, 'connect')
def pragmas(conn, _):
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=20000')

class Tx:
    def __init__(self, c): self.c = c
    def run(self, sql, args=()): return self.c.exec_driver_sql(sql, tuple(args))
    def one(self, sql, args=()):
        row = self.run(sql, args).mappings().first()
        return dict(row) if row else None
    def all(self, sql, args=()): return [dict(r) for r in self.run(sql, args).mappings().all()]

@contextmanager
def transaction():
    with engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            yield Tx(c)
            c.commit()
        except BaseException:
            c.rollback()
            raise

def get(t, key, default=None):
    r = t.one('SELECT value FROM settings WHERE key=?', (key,))
    return json.loads(r['value']) if r else default

def put(t, key, value):
    t.run('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value, ensure_ascii=False)))

def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def audit(t, kind, payload):
    previous = t.one('SELECT hash FROM audit ORDER BY id DESC LIMIT 1')
    prev = previous['hash'] if previous else '0'*64
    body = canonical({'kind': kind, 'time': time.time(), 'payload': payload})
    digest = hashlib.sha256((prev + body).encode()).hexdigest()
    t.run('INSERT INTO audit(body,prev,hash) VALUES(?,?,?)', (body, prev, digest))

def alert(t, code, evidence, severe=True):
    if not t.one('SELECT id FROM alerts WHERE code=? AND acknowledged=0', (code,)):
        t.run('INSERT INTO alerts(time,code,severity,evidence) VALUES(?,?,?,?)',
              (time.time(), code, 'CRITICAL' if severe else 'WARNING', canonical(evidence)))
        audit(t, 'alert', {'code':code, 'evidence':evidence})
    if severe: put(t, 'locked', True)

def heartbeat(t, service, instance):
    old = t.one('SELECT * FROM heartbeats WHERE service=?', (service,))
    if old and old['instance'] != instance:
        alert(t, 'RESTART_' + service, {'old':old['instance'], 'new':instance}, False)
    t.run('INSERT INTO heartbeats(service,time,instance) VALUES(?,?,?) ON CONFLICT(service) DO UPDATE SET time=excluded.time, instance=excluded.instance', (service,time.time(),instance))
