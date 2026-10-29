"""Projection history: how the projection for each future month has moved over time (Projection history tab).

Two kinds of snapshot, kept apart on the page:
  recorded       what the live dashboard projected on a given day (base case, no levers). Appended by refresh.py
                 every run into model/projection_log.json; Oct 16-29 2026 backfilled from the repo's git history.
  reconstructed  what TODAY's method would have projected on the 1st of each month of the current year, using only
                 data closed before that date (same refit as backtest.py, run out to the end of the horizon).
                 Shows how the outlook moved through the year; not what was on screen at the time.
KPIs per target month: total, ni, shop (Targets basis), amz, ddo, orders, ad (Meta + Google + Amazon ads).
Writes site/public/history.json (loaded only when the tab opens).
"""
import json, os, subprocess, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'projection_log.json')
OUT = os.path.join(HERE, '..', 'site', 'public', 'history.json')
KEYS = ['total', 'ni', 'shop', 'amz', 'ddo', 'orders', 'ad']


def snap_from_data(D):
    """Base-case KPIs per projected month from a data.json dict (same formulas as calc() on the page, levers at 0)."""
    fr, cg, ar = D['fin_ratio'], D['pnl']['cogs_pct'], D['pnl']['ad_ratio']
    out = {}
    for r in D['fut']:
        shop = r['orders'] * r['aov'] * fr
        amz, ddo = r['amazon'], r['ddo']
        ad = (r.get('meta') or 0) + (r.get('google') or 0) + (r.get('amz_ads') or 0)
        tot = shop + amz + ddo
        out[r['month']] = dict(total=round(tot), ni=round(tot * (1 - cg) - ad * ar - r['fixed']), shop=round(shop), amz=round(amz),
                               ddo=round(ddo), orders=round(r['orders']), ad=round(ad))
    return out


def load_log():
    return json.load(open(LOG)) if os.path.exists(LOG) else {'recorded': []}


def record(D, date, note=''):
    L = load_log()
    L['recorded'] = [s for s in L['recorded'] if s['date'] != date]
    L['recorded'].append(dict(date=date, partial=D.get('partial_month'), through=(D.get('nowcast') or {}).get('through'), note=note,
                              values=snap_from_data(D)))
    L['recorded'].sort(key=lambda s: s['date'])
    json.dump(L, open(LOG, 'w'), separators=(',', ':'))
    return L


def backfill_git():
    """One-off: last data.json committed on each day, from git history (needs a full clone)."""
    root = os.path.join(HERE, '..')
    log = subprocess.run(['git', 'log', '--format=%h %ad', '--date=format-local:%Y-%m-%d', '--', 'site/public/data.json'],
                         cwd=root, capture_output=True, text=True, env=dict(os.environ, TZ='America/Chicago')).stdout.split('\n')
    seen = {}
    for line in log:
        if not line.strip(): continue
        h, d = line.split()
        seen.setdefault(d, h)          # newest commit of each day (log is newest first)
    for d, h in sorted(seen.items()):
        D = json.loads(subprocess.run(['git', 'show', f'{h}:site/public/data.json'], cwd=root, capture_output=True, text=True).stdout)
        record(D, d, note=f'git {h}')


def reconstructed(year):
    import model as M
    import backtest as B
    from data import MONTHS, CUT
    M.FULL_HORIZON = True
    out = []
    for o in [m for m in MONTHS[:CUT] if m.startswith(str(year))] + ([MONTHS[CUT]] if CUT < len(MONTHS) and MONTHS[CUT].startswith(str(year)) else []):
        if o not in MONTHS[:CUT + 1]: continue
        fc = B.forecast_from(o)
        out.append(dict(date=o + '-01', values={ym: {k: round(v[k]) for k in ('total', 'ni', 'shop', 'ddo', 'orders', 'ad')} | {'amz': round(v['amz'])}
                                                 for ym, v in fc.items()}))
    M.FULL_HORIZON = False
    return out


def actuals():
    import backtest as B
    from data import MONTHS, CUT
    out = {}
    for ym in MONTHS[:CUT]:
        if ym < '2025-01': continue
        try:
            a = B.actual(ym)
            out[ym] = dict(total=round(a['total']), ni=round(a['ni']), shop=round(a['shop']), amz=round(a['amz']), ddo=round(a['ddo']),
                           orders=round(a['orders']), ad=round(a['ad']))
        except Exception:
            pass
    return out


def write(today):
    L = load_log()
    y = int(today[:4])
    H = dict(generated=today, keys=KEYS, recorded=L['recorded'], reconstructed=reconstructed(y), actual=actuals())
    json.dump(H, open(OUT, 'w'), separators=(',', ':'))
    return H


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--backfill':
        backfill_git()
    H = write(dt.date.today().isoformat() if len(sys.argv) < 3 else sys.argv[2])
    print(len(H['recorded']), 'recorded', [s['date'] for s in H['recorded']], len(H['reconstructed']), 'reconstructed', len(H['actual']), 'actual months')
    for s in H['recorded'] + H['reconstructed']:
        v = s['values']
        print(s['date'], {m: v[m]['total'] for m in ('2026-11', '2026-12') if m in v}, '2027:', sum(v[m]['total'] for m in v if m.startswith('2027')) if any(m.startswith('2027') for m in v) else None)
