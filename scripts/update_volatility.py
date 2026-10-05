"""Observed-price volatility dashboard. No padding or invented prices."""
import json, math, statistics, re, io, urllib.request, urllib.parse, time
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from openpyxl import load_workbook

HEADERS={'User-Agent':'Mozilla/5.0 CHRIS-Investment-Brief/3.0','Accept':'*/*'}
SECTORS=[('通訊服務','XLC'),('非必需消費','XLY'),('必需消費','XLP'),('能源','XLE'),('金融','XLF'),('醫療保健','XLV'),('工業','XLI'),('資訊科技','XLK'),('原材料','XLB'),('房地產','XLRE'),('公用事業','XLU')]
NOW=datetime.now(timezone.utc)
TODAY=NOW.astimezone(ZoneInfo('America/New_York')).date()

def download(url):
    req=urllib.request.Request(url,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=20) as r:return r.read()

def holdings_spdr(ticker):
    url='https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-'+ticker.lower()+'.xlsx'
    sheet=load_workbook(io.BytesIO(download(url)),read_only=True,data_only=True).active
    rows=list(sheet.values)
    stamp=next(str(r[1]) for r in rows if r and r[0]=='Holdings:')
    day=datetime.strptime(stamp.replace('As of ','').strip(),'%d-%b-%Y').date()
    age=(TODAY-day).days
    if age<0:raise ValueError('Future holdings date')
    header=next(i for i,r in enumerate(rows) if r[:2]==('Name','Ticker'))
    items=[]
    for r in rows[header+1:]:
        if len(r)<4 or not r[1]:continue
        t=str(r[1]).strip()
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',t):continue
        items.append(dict(ticker=t.replace('.','-'),company=str(r[0]),sector=str(r[5]) if len(r)>5 and r[5] not in (None,'-') else '未分類',roster=ticker,roster_date=day.isoformat()))
    if not items:raise ValueError('Empty holdings')
    return dict(name=ticker,status='STALE' if age>7 else 'VERIFIED',as_of=day.isoformat(),url=url,items=items)

def holdings_qqq():
    url='https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46090E103/holdings/fund?idType=cusip&productType=ETF'
    p=json.loads(download(url))
    if p.get('cusip')!='46090E103':raise ValueError('Wrong QQQ identifier')
    day=datetime.strptime(p['effectiveBusinessDate'],'%Y-%m-%d').date()
    age=(TODAY-day).days
    if age<0:raise ValueError('Future QQQ holdings')
    items=[]
    for r in p['holdings']:
        t=str(r.get('ticker','')).strip()
        if r.get('securityTypeName') not in ('Common Stock','REIT','American Depository Receipt') or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',t):continue
        items.append(dict(ticker=t.replace('.','-'),company=r['issuerName'],sector='未分類',roster='QQQ',roster_date=day.isoformat()))
    if not items:raise ValueError('Empty QQQ equity roster')
    return dict(name='QQQ',status='STALE' if age>7 else 'VERIFIED',as_of=day.isoformat(),url=url,items=items)

def metric_window(prices,n):
    if len(prices)<n+1 or any(v is None for v in prices[-n-1:]):return None
    r=[math.log(b/a) for a,b in zip(prices[-n-1:-1],prices[-n:])]
    return statistics.stdev(r)*math.sqrt(252)

def calculate(points):
    values=[p[1] for p in points]
    result={f'vol_{n}d':metric_window(values,n) for n in (20,30,60,90)}
    result['return_90d']=None;result['max_drawdown_90d']=None
    if len(values)>=91 and all(v is not None for v in values[-91:]):
        window=values[-91:];result['return_90d']=window[-1]/window[0]-1
        peak=window[0];drawdown=0
        for v in window:peak=max(peak,v);drawdown=min(drawdown,v/peak-1)
        result['max_drawdown_90d']=drawdown
    result['vol_ratio_20d_90d']=result['vol_20d']/result['vol_90d'] if result['vol_20d'] is not None and result['vol_90d'] is not None and result['vol_90d']>0 else None
    rolling=[]
    for i in range(90,len(values)):
        v=metric_window(values[:i+1],90)
        rolling.append(dict(date=points[i][0],value=v))
    result['rolling_90d']=rolling[-120:]
    result['prices']=[dict(date=d,value=v) for d,v in points[-120:]]
    result['price_days']=sum(v is not None for v in values)
    return result

def observed_prices(ticker):
    url='https://query1.finance.yahoo.com/v8/finance/chart/'+urllib.parse.quote(ticker,safe='')+'?range=1y&interval=1d'
    p=json.loads(download(url))['chart']['result'][0]
    if p['meta'].get('symbol','').upper()!=ticker.upper():raise ValueError('Quote symbol mismatch')
    stamps=p.get('timestamp',[])
    adjusted=p['indicators'].get('adjclose')
    if not adjusted:raise ValueError('No observed adjusted-close series')
    values=adjusted[0]['adjclose'];closes=p['indicators']['quote'][0]['close']
    tz=ZoneInfo(p['meta']['exchangeTimezoneName'])
    end=p['meta'].get('currentTradingPeriod',{}).get('regular',{}).get('end')
    points=[];raw_last=None
    for stamp,adjusted_close,close in zip(stamps,values,closes):
        day=datetime.fromtimestamp(stamp,timezone.utc).astimezone(tz).date()
        if day>TODAY:continue
        if day==NOW.astimezone(tz).date() and (not end or NOW.timestamp()<end):continue
        v=float(adjusted_close) if adjusted_close is not None else None
        if v is not None and (not math.isfinite(v) or v<=0):v=None
        points.append((day.isoformat(),v))
        raw_last=float(close) if close is not None and math.isfinite(float(close)) else None
    if not points or points[-1][1] is None:raise ValueError('Latest adjusted close missing')
    return points,raw_last

def stock_record(item):
    row=dict(item,status='SOURCE ERROR',as_of='—',valid=False,source='Yahoo Finance · observed adjusted daily closes',price=None)
    try:
        points,price=observed_prices(item['ticker'])
        row.update(calculate(points),price=price,as_of=points[-1][0])
        age=(TODAY-datetime.strptime(row['as_of'],'%Y-%m-%d').date()).days
        row['status']='STALE' if age>7 else 'VERIFIED' if row['vol_90d'] is not None else 'PARTIAL'
        row['valid']=row['status']=='VERIFIED'
    except Exception as e:row['error']=str(e)
    return row

def main():
    sources=[];members={};sector_map={}
    def roster_job(name):
        try:return holdings_qqq() if name=='QQQ' else holdings_spdr(name)
        except Exception as e:return dict(name=name,status='SOURCE ERROR',as_of='—',items=[],error=str(e))
    names=['SPY','QQQ']+[t for _,t in SECTORS]
    with ThreadPoolExecutor(max_workers=4) as pool:rosters=list(pool.map(roster_job,names))
    for r in rosters:
        sources.append({k:v for k,v in r.items() if k!='items'})
        if r['name'] in ('SPY','QQQ'):
            for item in r['items']:
                old=members.setdefault(item['ticker'],dict(item,membership=[]))
                old['membership'].append(r['name'])
        else:
            sector=next(n for n,t in SECTORS if t==r['name'])
            for item in r['items']:sector_map.setdefault(item['ticker'],set()).add(sector)
    for t,item in members.items():
        choices=sector_map.get(t,set())
        if len(choices)==1:item.update(sector=next(iter(choices)),sector_source='SPDR sector ETF holdings membership')
        elif len(choices)>1:item.update(sector='未分類',sector_source='Conflicting sector ETF memberships')
    stocks=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs=[pool.submit(stock_record,item) for item in members.values()]
        for i,future in enumerate(as_completed(jobs),1):
            stocks.append(future.result())
            if i%50==0:print('Observed volatility records',i,'/',len(jobs),flush=True)
    stocks.sort(key=lambda x:x['ticker'])
    good=[x for x in stocks if x['valid']]
    summaries=[]
    for name,_ in SECTORS:
        values=[x['vol_90d'] for x in good if x['sector']==name]
        summaries.append(dict(name=name,count=len(values),mean=statistics.mean(values) if values else None,median=statistics.median(values) if values else None,status='VERIFIED' if values else 'NO FEED'))
    bins=[0]*8
    for row in good:bins[min(7,int(row['vol_90d']/.2))]+=1
    result=dict(updated_at=NOW.isoformat(),status='VERIFIED' if len(good)==len(stocks) and stocks and all(r['status']=='VERIFIED' for r in sources) else 'PARTIAL' if stocks else 'SOURCE ERROR',universe='SPY + QQQ official equity holdings, deduplicated; ETF holdings proxy, not an official index constituent feed',sources=sources,stocks=stocks,sectors=summaries,distribution=dict(labels=['0–20%','20–40%','40–60%','60–80%','80–100%','100–120%','120–140%','≥140%'],counts=bins),method=dict(hv='Sample stddev(log(adjusted close[t]/adjusted close[t-1])) × sqrt(252)',windows=[20,30,60,90],return_90d='adjusted close[t] / adjusted close[t-90] - 1',max_drawdown_90d='minimum(price / running peak - 1) over 91 adjusted closes',missing='Null observations are never skipped to bridge gaps. Incomplete windows return null.',sector='Official SPDR sector ETF membership; unclassified QQQ-only stocks remain unclassified.',prices='Latest completed exchange-day bar only; provider-adjusted historical closes, not fabricated prices.'),coverage=dict(total=len(stocks),valid=len(good),latest_date=max((s['as_of'] for s in good),default='—')))
    Path('data').mkdir(exist_ok=True)
    Path('data/volatility.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result['coverage']),flush=True)
if __name__=='__main__':main()
