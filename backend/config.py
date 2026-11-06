import json, os, secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('TRADINGLAB_DATA', str(ROOT / 'data'))).resolve()
LOCAL = Path(os.environ.get('TRADINGLAB_LOCAL', str(ROOT / '.local'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
LOCAL.mkdir(parents=True, exist_ok=True)
DB = DATA / 'tradinglab.sqlite3'
RISK_URL = os.environ.get('TRADINGLAB_RISK_URL', 'http://127.0.0.1:8766')
ORIGINS = {'http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8765'}
if os.environ.get('TRADINGLAB_UI_ORIGIN'):
    ORIGINS.add(os.environ['TRADINGLAB_UI_ORIGIN'])

def credentials():
    path = LOCAL / 'secrets.json'
    if not path.exists():
        # Exclusive creation prevents parallel startup from rotating credentials.
        try:
            with path.open('x', encoding='utf-8') as f:
                json.dump({k: secrets.token_urlsafe(32) for k in ('client', 'service', 'signing')}, f)
        except FileExistsError:
            pass
    return json.loads(path.read_text(encoding='utf-8'))

DEFAULT_RULES = dict(version=1, per_order=.05, per_symbol=.15, total=.50,
    daily_loss=.01, drawdown=.05, daily_proposals=20, quote_ttl=60,
    heartbeat_ttl=15, approval_ttl=30, fee_rate=.001425, min_fee=20,
    stock_tax=.003, etf_tax=.001, slippage=.001, participation=.001,
    whitelist={'0050': 'ETF', '006208': 'ETF', '2330': 'STOCK'})
