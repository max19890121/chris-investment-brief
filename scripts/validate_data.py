import json,math
from pathlib import Path

def finite(value):
    if isinstance(value,float):assert math.isfinite(value), 'Non-finite JSON value'
    elif isinstance(value,dict):
        for item in value.values():finite(item)
    elif isinstance(value,list):
        for item in value:finite(item)
brief=json.loads(Path('data/brief.json').read_text())
required=['markets','global_focus','headlines','fear_greed','economic_actuals','economic_calendar','fomc','fed_policy','fedwatch','key_levels','core_observations','move_history','level_history','risk_regime','spreads']
assert all(k in brief for k in required)
assert len(brief['level_history'])==5
assert len(brief['sectors'])==11
assert len(brief['spreads'])==4
for spread in brief['spreads']:
    if spread['status']=='VERIFIED':
        assert spread['value_bp'] is not None
        assert len({x['as_of'] for x in spread['inputs'].values()})==1
        assert all(x['status']=='VERIFIED' for x in spread['inputs'].values())
vol=json.loads(Path('data/volatility.json').read_text())
assert len({x['ticker'] for x in vol['stocks']})==len(vol['stocks'])
for row in vol['stocks']:
    if row['valid']:
        assert row['vol_90d'] is not None and row['price_days']>=91
    assert row.get('max_drawdown_90d') is None or -1<=row['max_drawdown_90d']<=0
finite(brief);finite(vol)
print('PASS: complete brief schema, five histories, eleven sectors, synchronized derived spreads, unique universe, no fabricated null values')
