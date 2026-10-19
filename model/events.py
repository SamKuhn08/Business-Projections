"""Promo / event layer. Measures each movable promo's lift from daily Shopify orders, so the model can
learn demand from promo-free history and add promos back on the dates in promos.json.

Lift of one event = sum over the sale days plus the 7 days after (post-sale dip counts against it) of
(actual - baseline), baseline = median of promo-free days in the 4 weeks before and weeks 2-5 after.
Expressed as 'baseline days': extra orders / baseline orders per day, so it scales with the business.
Nov-Dec sales (BFCM, holiday) run every year and stay inside the seasonal index."""
import json, os, datetime as dt, statistics as st
HERE = os.path.dirname(__file__)
P = json.load(open(os.path.join(HERE, 'promos.json')))['events']
DAILY = json.load(open(os.path.join(HERE, 'shop_daily.json')))          # {"YYYY-MM-DD": [orders, net_sales]}
SEASONAL = {'bfcm', 'holiday'}
POST = 7
D = lambda s: dt.date.fromisoformat(s)


def days(a, b):
    d = D(a)
    while d <= D(b):
        yield d.isoformat(); d += dt.timedelta(1)


PROMO_DAYS = {d for e in P for d in days(e['start'], e['end'])}


def measure(e, upto=None):
    """Measured lift of one past event using daily data up to `upto` (ISO date, exclusive)."""
    s, en = D(e['start']), D(e['end'])
    last = (en + dt.timedelta(POST)).isoformat()
    if upto and last >= upto: return None
    if last not in DAILY: return None
    win = [(s + dt.timedelta(k)).isoformat() for k in range(-28, 0)] + [(en + dt.timedelta(k)).isoformat() for k in range(8, 36)]
    base = [DAILY[d] for d in win if d in DAILY and d not in PROMO_DAYS and (not upto or d < upto)]
    if len(base) < 10: return None
    bo = st.median(b[0] for b in base); br = st.median(b[1] for b in base)
    by_month = {}
    xo = xr = 0.0
    for d in days(e['start'], last):
        if d in PROMO_DAYS and not (e['start'] <= d <= e['end']): continue   # another sale's day
        o, r = DAILY[d]
        by_month.setdefault(d[:7], [0.0, 0.0])
        by_month[d[:7]][0] += o - bo; by_month[d[:7]][1] += r - br
        xo += o - bo; xr += r - br
    return dict(type=e['type'], start=e['start'], end=e['end'], pro=e.get('pro'), base_o=bo, base_r=br,
                extra_o=xo, extra_r=xr, lift=xo / bo, lift_r=xr / br, by_month=by_month)


def history_adjustment(upto=None):
    """{month: [extra_orders, extra_revenue]} from movable promos, for months before `upto`."""
    adj = {}
    for e in P:
        if e['type'] in SEASONAL: continue
        m = measure(e, upto)
        if not m: continue
        for mon, (o, r) in m['by_month'].items():
            a = adj.setdefault(mon, [0.0, 0.0]); a[0] += o; a[1] += r
    return adj


def type_lifts(upto=None):
    """Lift per $100 off the 3.0 Pro, in baseline days (orders, revenue), per event type: recency-weighted
    mean over documented sales (2024 on) finished before `upto`. Types with fewer than 2 such sales use the
    pooled rate. Gift-only sales (pro 0) and sales without a Pro discount do not set the rate."""
    pts = {}
    for e in P:
        if e['type'] in SEASONAL or e.get('inferred') or not e.get('pro'): continue
        m = measure(e, upto)
        if not m: continue
        w = int(e['start'][:4]) - 2023
        pts.setdefault(e['type'], []).append((m['lift'] * 100 / e['pro'], m['lift_r'] * 100 / e['pro'], w))
    wavg = lambda v, k: sum(x[k] * x[2] for x in v) / sum(x[2] for x in v)
    allp = [x for v in pts.values() for x in v]
    pooled = (wavg(allp, 0), wavg(allp, 1), len(allp)) if allp else (0, 0, 0)
    out = {t: ((wavg(v, 0), wavg(v, 1), len(v)) if len(v) >= 2 else pooled) for t, v in pts.items()}
    out['_pooled'] = pooled
    return out


def event_lift(e, lifts):
    """(orders, revenue) lift in baseline days for a future event: rate for its type x its Pro discount / 100."""
    rate = lifts.get(e['type'], lifts.get('_pooled', (0, 0, 0)))
    k = (e.get('pro') or 0) / 100
    return rate[0] * k, rate[1] * k


def future_events(after):
    """Movable events whose sale days fall on or after `after` (ISO date): [(type, {month: share_of_days})]."""
    out = []
    for e in P:
        if e['type'] in SEASONAL or e['end'] < after: continue
        ds = [d for d in days(e['start'], e['end']) if d >= after]
        if not ds: continue
        sh = {}
        for d in ds: sh[d[:7]] = sh.get(d[:7], 0) + 1 / len(list(days(e['start'], e['end'])))
        out.append((e['type'], sh, e))
    return out


if __name__ == '__main__':
    for e in P:
        if e['type'] in SEASONAL: continue
        m = measure(e)
        if m: print(f"{m['type']:13} {m['start']}..{m['end']} pro {str(m['pro']):>4}  base {m['base_o']:5.1f}/day  extra {m['extra_o']:6.0f} orders ${m['extra_r']:>8,.0f}  lift {m['lift']:5.1f} days (rev {m['lift_r']:5.1f})")
    print({k: tuple(round(x, 1) for x in v) for k, v in type_lifts().items()})


# ---------------------------------------------------------------- Amazon: Prime events only
# Measured from Seller Board (amazon_events.json): event days + the week after vs the 4 weeks before and
# weeks 2-5 after. Shopify-only style sales (Presidents, spring) showed no Amazon lift, so only Prime moves Amazon.
AMZ_EV = json.load(open(os.path.join(HERE, 'amazon_events.json')))
AMZ_TYPES = {'prime_summer', 'prime_oct'}


def amazon_history_adjustment(upto=None):
    """{month: extra Amazon sales (Seller Board gross)} from Prime events finished before `upto`."""
    adj = {}
    for e in AMZ_EV:
        if upto and (D(e['end']) + dt.timedelta(POST)).isoformat() >= upto: continue
        adj[e['start'][:7]] = adj.get(e['start'][:7], 0) + e['extra']
    return adj


def amazon_lifts(upto=None):
    """Recency-weighted lift in baseline days per Prime type, from events finished before `upto`."""
    pts = {}
    for e in AMZ_EV:
        if upto and (D(e['end']) + dt.timedelta(POST)).isoformat() >= upto: continue
        pts.setdefault(e['type'], []).append((e['lift_days'], int(e['start'][:4]) - 2022))
    return {t: sum(l * w for l, w in v) / sum(w for _, w in v) for t, v in pts.items()}


def amazon_future(after):
    """Future Prime events (Amazon dates) on or after `after`: [(type, month)]."""
    out = []
    for e in P:
        if e['type'] not in AMZ_TYPES: continue
        a = e.get('amazon') or [e['start'], e['end']]
        if a[0] >= after: out.append((e['type'], a[0][:7]))
    return out
