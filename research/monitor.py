"""FIFO realized outcomes attributed to the approved model on entry, after fees."""
import json,statistics

def realized(t,model_id):
    lots={}; completed=[]
    for fill in t.all('SELECT f.*,o.payload FROM fills f JOIN orders o ON o.id=f.order_id ORDER BY f.id'):
        p=json.loads(fill['payload']);queue=lots.setdefault(p['symbol'],[])
        if p['side']=='BUY':
            queue.append({'remaining':fill['quantity'],'entry_quantity':fill['quantity'],'cost':fill['price']+fill['fee']/fill['quantity'],
                'model':p.get('model_id'),'pnl':0})
        else:
            remaining=fill['quantity'];proceeds=fill['price']-(fill['fee']+fill['tax'])/fill['quantity']
            while remaining and queue:
                lot=queue[0];qty=min(remaining,lot['remaining']);lot['pnl']+=qty*(proceeds-lot['cost'])
                lot['remaining']-=qty;remaining-=qty
                if lot['remaining']==0:
                    if lot['model']==model_id:completed.append(lot['pnl']/(lot['cost']*lot['entry_quantity']))
                    queue.pop(0)
    sufficient=len(completed)>=30
    drift=False
    if len(completed)>=60:
        old=completed[:-30];new=completed[-30:]
        drift=statistics.mean(new)<statistics.mean(old)-2*statistics.stdev(old)
    return {'status':'AVAILABLE' if sufficient else 'N/A','closed_entry_lots':len(completed),
        'mean_net_return':statistics.mean(completed) if sufficient else None,
        'win_rate':sum(v>0 for v in completed)/len(completed) if sufficient else None,
        'performance_drift':drift,'reason':'FIFO 已平倉進場批次，至少 30 筆才顯示統計；60 筆才比較近期變化。不自動換模型。'}
