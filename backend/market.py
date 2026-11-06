import csv, io, json, hashlib, time, math, random
from datetime import date, timedelta
from pathlib import Path
import httpx
from backend.db import canonical, get, put, audit
from backend.config import DATA

def parse_csv(content):
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    required = {'date','open','high','low','close','volume'}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError('CSV 必須包含 date,open,high,low,close,volume')
    rows, previous = [], ''
    for n, row in enumerate(reader, 2):
        try:
            day = date.fromisoformat(row['date']).isoformat()
            values = [float(row[k]) for k in ('open','high','low','close')]
            volume = int(row['volume'])
            o,h,l,c = values
            if day <= previous: raise ValueError('日期重複或未依時間遞增')
            if not all(math.isfinite(v) and v > 0 for v in values): raise ValueError('價格必須為有限正數')
            if not l <= min(o,c) <= max(o,c) <= h or volume < 0: raise ValueError('OHLC 或成交量不合理')
            rows.append(dict(date=day,open=o,high=h,low=l,close=c,volume=volume))
            previous = day
        except (ValueError,TypeError,KeyError) as e:
            raise ValueError(f'第 {n} 行：{e}') from e
    if len(rows) < 2: raise ValueError('至少需要兩筆日線')
    if len(rows) > 100000: raise ValueError('每次匯入最多 100,000 筆')
    return rows

def save_dataset(t, symbol, rows, source, mode='REPLAY', adjusted=False):
    digest = hashlib.sha256(canonical({'symbol':symbol,'rows':rows,'adjusted':adjusted}).encode()).hexdigest()
    dataset = digest[:24]
    meta = dict(timezone='Asia/Taipei',frequency='daily',delayed=True,adjusted=adjusted,
        corporate_actions='未提供公司行動；不含股息再投資，跨除權息績效有限制',
        calendar='僅以資料內明確存在之交易日推進；不推定週一至週五皆開市', rows=len(rows), start=rows[0]['date'],end=rows[-1]['date'])
    if not t.one('SELECT id FROM datasets WHERE id=?',(dataset,)):
        t.run('INSERT INTO datasets VALUES(?,?,?,?,?,?,?)',(dataset,symbol,source,mode,time.time(),digest,canonical(meta)))
        for i,b in enumerate(rows):
            t.run('INSERT INTO bars VALUES(?,?,?,?,?,?,?,?)',(dataset,i,b['date'],b['open'],b['high'],b['low'],b['close'],b['volume']))
        audit(t,'dataset_import',{'id':dataset,'source':source,'rows':len(rows)})
    return dataset

def demo_rows(seed=42, count=540):
    rng = random.Random(seed)
    rows, price, day = [], 40.0, date(2023,1,1)
    # Explicit synthetic sessions: this is not an exchange holiday calendar.
    while len(rows) < count:
        day += timedelta(days=1)
        if day.weekday() >= 5 or (day.month == 2 and day.day in range(8,15)): continue
        i=len(rows)
        o=round(price*(1+rng.uniform(-.006,.006)),2)
        price=round(max(5,o*(1+.0018*math.sin(i/23)+rng.gauss(.0003,.008))),2)
        rows.append(dict(date=day.isoformat(),open=o,high=round(max(o,price)*1.012,2),
            low=round(min(o,price)*.988,2),close=price,volume=rng.randint(20000,80000)))
    return rows

def active(t): return get(t,'active',{})
def quote(t, symbol):
    item = active(t).get(symbol)
    if not item: return None
    b = t.one('SELECT * FROM bars WHERE dataset=? AND idx=?',(item['dataset'],get(t,'cursor',60)))
    if b:
        d=t.one('SELECT * FROM datasets WHERE id=?',(item['dataset'],))
        b.update(symbol=symbol,source=d['source'],mode=d['mode'],received=d['received'],metadata=json.loads(d['metadata']))
    return b

def twse_month(symbol, month):
    if len(month)!=8 or not month.isdigit() or month[-2:]!='01': raise ValueError('月份格式 YYYYMM01')
    date(int(month[:4]),int(month[4:6]),1)
    cache=DATA/'cache'; cache.mkdir(exist_ok=True)
    path=cache/f'{symbol}-{month}.json'
    stamp=cache/'last_request'
    if path.exists(): obj=json.loads(path.read_text(encoding='utf-8'))
    else:
        last=float(stamp.read_text()) if stamp.exists() else 0
        if time.time()-last < 10: raise ValueError('公開資料請求間隔至少 10 秒，請稍後重試')
        stamp.write_text(str(time.time()))
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            r=c.get('https://www.twse.com.tw/exchangeReport/STOCK_DAY',params={'response':'json','date':month,'stockNo':symbol})
            r.raise_for_status(); obj=r.json()
        if obj.get('stat')!='OK': raise ValueError('TWSE 未回傳可用資料：'+str(obj.get('stat')))
        path.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
    buf=io.StringIO(); w=csv.writer(buf); w.writerow(['date','open','high','low','close','volume'])
    for row in obj['data']:
        y,m,d=map(int,row[0].split('/'))
        w.writerow([date(y+1911,m,d).isoformat(),*[str(row[i]).replace(',','') for i in (3,4,5,6,1)]])
    return parse_csv(buf.getvalue())
