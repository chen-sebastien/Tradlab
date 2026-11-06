"""Initial durable simulator schema."""
from alembic import op
revision = '0001'
down_revision = None

def upgrade():
    statements = [
      'CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
      'CREATE TABLE datasets(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,source TEXT NOT NULL,mode TEXT NOT NULL,received REAL NOT NULL,hash TEXT NOT NULL,metadata TEXT NOT NULL)',
      'CREATE TABLE bars(dataset TEXT NOT NULL REFERENCES datasets(id),idx INTEGER NOT NULL,date TEXT NOT NULL,open REAL NOT NULL,high REAL NOT NULL,low REAL NOT NULL,close REAL NOT NULL,volume INTEGER NOT NULL,PRIMARY KEY(dataset,idx),UNIQUE(dataset,date))',
      'CREATE TABLE orders(id TEXT PRIMARY KEY,idem TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,bar INTEGER NOT NULL,filled INTEGER NOT NULL DEFAULT 0,reserved REAL NOT NULL DEFAULT 0,approval TEXT,expires REAL,reason TEXT,rule_version INTEGER,fee REAL NOT NULL DEFAULT 0)',
      'CREATE TABLE fills(id INTEGER PRIMARY KEY,order_id TEXT NOT NULL REFERENCES orders(id),bar INTEGER NOT NULL,quantity INTEGER NOT NULL,price REAL NOT NULL,fee REAL NOT NULL,tax REAL NOT NULL,time REAL NOT NULL,UNIQUE(order_id,bar))',
      'CREATE TABLE positions(symbol TEXT PRIMARY KEY,quantity INTEGER NOT NULL,cost REAL NOT NULL)',
      'CREATE TABLE ledger(id INTEGER PRIMARY KEY,fill_id INTEGER UNIQUE REFERENCES fills(id),delta REAL NOT NULL,reason TEXT NOT NULL)',
      'CREATE TABLE heartbeats(service TEXT PRIMARY KEY,time REAL NOT NULL,instance TEXT NOT NULL)',
      'CREATE TABLE alerts(id INTEGER PRIMARY KEY,time REAL NOT NULL,code TEXT NOT NULL,severity TEXT NOT NULL,evidence TEXT NOT NULL,acknowledged INTEGER NOT NULL DEFAULT 0)',
      'CREATE TABLE audit(id INTEGER PRIMARY KEY,body TEXT NOT NULL,prev TEXT NOT NULL,hash TEXT NOT NULL)',
      'CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,key TEXT UNIQUE NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,params TEXT NOT NULL,result TEXT,error TEXT,progress INTEGER NOT NULL DEFAULT 0)',
      'CREATE TABLE models(id TEXT PRIMARY KEY,job_id TEXT UNIQUE NOT NULL,dataset TEXT NOT NULL,report TEXT NOT NULL,artifact TEXT NOT NULL,approved INTEGER NOT NULL DEFAULT 0)',
      'CREATE TABLE holdouts(key TEXT PRIMARY KEY,used REAL NOT NULL,job_id TEXT NOT NULL)',
      'CREATE TABLE rule_versions(version INTEGER PRIMARY KEY,time REAL NOT NULL,rules TEXT NOT NULL)',
    ]
    for sql in statements: op.execute(sql)

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup.')
