import math, json, subprocess, hashlib, time
import numpy as np
import pandas as pd
import joblib
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import balanced_accuracy_score
from backend.config import DATA
from backend.domain import costs
from backend.db import canonical

def features(frame):
    c=frame['close']
    return pd.DataFrame({'return_1':c.pct_change(),'return_5':c.pct_change(5),
        'trend_20':c/c.rolling(20).mean()-1,'volatility_10':c.pct_change().rolling(10).std(),
        'volume_ratio':frame['volume']/frame['volume'].rolling(20).mean()-1}).replace([np.inf,-np.inf],np.nan)

def trend_signal(closes,fast=5,slow=20):
    if len(closes)<slow: return 'HOLD'
    fast_ma=float(np.mean(closes[-fast:])); slow_ma=float(np.mean(closes[-slow:]))
    if fast_ma<slow_ma: return 'SELL'
    if fast_ma>slow_ma and closes[-1]<closes[-2]: return 'BUY'
    return 'HOLD'

def backtest(rows,rules,kind='ETF',strategy='trend',fast=5,slow=20):
    if len(rows)<slow+3: raise ValueError('回測資料不足，至少需要 slow + 3 筆日線')
    if not 2<=fast<slow<=120: raise ValueError('需 2 ≤ fast < slow ≤ 120')
    def simulate(baseline=False):
        cash=100000.; qty=0; entry=0; entry_fee=0; peak=100000.; locked=False
        curve=[]; trades=[]; fees=0.; turnover=0.; exposure=[]; closed=[]
        closes=[b['close'] for b in rows]
        pending='HOLD'
        for i,b in enumerate(rows):
            if i>=slow:
                signal='BUY' if baseline and i==slow else pending if not baseline else 'HOLD'
                # Signal from previous close; next open with adverse slippage.
                if signal=='BUY' and qty==0 and not locked:
                    price=b['open']*(1+rules['slippage'])
                    nav=cash
                    q=int(nav*min(rules['per_order'],rules['per_symbol'],rules['total'])/price)
                    q=min(q,int(b['volume']*rules['participation']))
                    fee,tax=costs(q*price,'BUY',kind,rules)
                    if q and q*price+fee<=cash:
                        qty=q; entry=price; entry_fee=fee; cash-=q*price+fee
                        fees+=fee; turnover+=q*price
                        trades.append(dict(date=b['date'],side='BUY',quantity=q,price=price,fee=fee,tax=0))
                elif signal=='SELL' and qty:
                    price=b['open']*(1-rules['slippage'])
                    fee,tax=costs(qty*price,'SELL',kind,rules)
                    pnl=qty*(price-entry)-entry_fee-fee-tax
                    cash+=qty*price-fee-tax; fees+=fee+tax; turnover+=qty*price
                    closed.append(pnl); trades.append(dict(date=b['date'],side='SELL',quantity=qty,price=price,fee=fee,tax=tax,pnl=pnl)); qty=0
                nav=cash+qty*b['close']; peak=max(peak,nav)
                if 1-nav/peak>=rules['drawdown']: locked=True
                curve.append({'date':b['date'],'nav':round(nav,2),'gross_nav':round(nav+fees,2)})
                exposure.append(qty*b['close']/nav)
            pending=trend_signal(closes[:i+1],fast,slow)
        # Both portfolios marked to close, no artificial final liquidation.
        vals=[100000]+[c['nav'] for c in curve]; peaks=np.maximum.accumulate(vals)
        winners=[v for v in closed if v>0]; losers=[v for v in closed if v<0]
        return dict(curve=curve,trades=trades,metrics=dict(net_return=vals[-1]/100000-1,gross_return=(vals[-1]+fees)/100000-1,
            max_drawdown=float(max(1-np.array(vals)/peaks)),win_rate=len(winners)/len(closed) if closed else None,
            average_win=float(np.mean(winners)) if winners else None,average_loss=float(np.mean(losers)) if losers else None,
            profit_factor=sum(winners)/abs(sum(losers)) if losers else None,trade_count=len(trades),closed_trades=len(closed),
            exposure=float(np.mean(exposure)),turnover=turnover/100000,costs=round(fees,2),open_quantity=qty),
            warnings=['少於 30 筆已平倉交易，樣本不足' if len(closed)<30 else '', '不含公司行動／股息；未平倉按最後收盤估值',
                '扣費前仍包含滑價；日線僅模擬下一開盤，不推定棒內先後', '固定單一 5% 上限資金區塊，基準使用相同投入與費用；無停利停損棒內撮合'])
    return {'strategy':simulate(strategy=='hold'),'benchmark':simulate(True),'parameters':{'fast':fast,'slow':slow,'rules':rules,'strategy':strategy}}

def train(rows,dataset,job_id,code_version):
    frame=pd.DataFrame(rows)
    if len(frame)<250: return {'skipped':True,'reason':'資料少於 250 筆，不訓練'}
    X=features(frame).iloc[20:-5].reset_index(drop=True)
    y=(frame['close'].shift(-5)>frame['close']).astype(int).iloc[20:-5].reset_index(drop=True)
    dates=frame['date'].iloc[20:-5].reset_index(drop=True)
    cut=int(len(X)*.8); gap=5
    development=X.iloc[:cut-gap]; labels=y.iloc[:cut-gap]
    folds=[]
    def model(): return Pipeline([('imputer',SimpleImputer(strategy='median')),('scaler',StandardScaler()),('model',LogisticRegression(random_state=42,max_iter=1000,C=1.0))])
    for a,b in TimeSeriesSplit(n_splits=4,gap=gap).split(development):
        if len(set(labels.iloc[a]))<2: raise ValueError('訓練區間只有一個標籤類別')
        m=model(); m.fit(development.iloc[a],labels.iloc[a]); pred=m.predict(development.iloc[b])
        majority=int(labels.iloc[a].mean()>=.5)
        folds.append(dict(train=[dates.iloc[a[0]],dates.iloc[a[-1]]],validation=[dates.iloc[b[0]],dates.iloc[b[-1]]],
            score=float(balanced_accuracy_score(labels.iloc[b],pred)),baseline=float(balanced_accuracy_score(labels.iloc[b],np.full(len(b),majority))),gap=gap))
    score=float(np.mean([f['score'] for f in folds])); baseline=float(np.mean([f['baseline'] for f in folds]))
    m=model(); m.fit(development,labels)
    # Holdout is measured once for reporting; it is never an acceptance criterion.
    holdout=float(balanced_accuracy_score(y.iloc[cut:],m.predict(X.iloc[cut:])))
    eligible=score>=.52 and score>baseline+.01
    model_id=hashlib.sha256((dataset+code_version+'logistic-v1').encode()).hexdigest()[:20]
    directory=DATA/'models'; directory.mkdir(exist_ok=True)
    artifact=directory/(model_id+'.joblib'); joblib.dump(m,artifact)
    recent=X.iloc[-60:]; reference=development
    drift=((recent.mean()-reference.mean()).abs()/(reference.std()+1e-9)).to_dict()
    return dict(id=model_id,artifact=str(artifact),eligible=eligible,score=score,baseline=baseline,holdout_score=holdout,
        folds=folds,holdout=[dates.iloc[cut],dates.iloc[-1]],holdout_used=time.time(),dataset=dataset,code_version=code_version,
        seed=42,params={'C':1.,'horizon':5,'gap':5,'features':list(X.columns)},train=[dates.iloc[0],dates.iloc[cut-gap-1]],available_after=dates.iloc[cut-1],
        missing_rate=features(frame).isna().mean().to_dict(),drift=drift,drift_warning=any(v>2 for v in drift.values()),
        realized_performance={'status':'N/A','reason':'尚無此模型已平倉實績；分類準確度不是交易勝率'},
        limitations=['合成行情的驗證分數不代表市場效果','固定參數，未調參；保留測試集僅使用一次','候選不會自動上線，人工核准只適用模擬提案'])
