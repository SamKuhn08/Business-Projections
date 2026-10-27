"""Daily forecast for the BFCM + Holiday season (Nov and Dec), per channel.

Sam (2026-10-27): build Nov and Dec day by day from history and the promo calendar, so the ramp into BFCM, the
Turkey Five (Thanksgiving to Cyber Monday), where BFCM falls in the calendar, and the Holiday-sale dip below BFCM
(but above MSRP) are explicit. Nov 1 2026 is still MSRP. Veterans Day 2026 (supplements, shirts, accessories) is
not treated as a revenue event.

Method, per channel (Shopify net sales, Shopify orders, Amazon sales):
  base   = average full-price day of Sep-Oct that year (no sale day, and not within 7 days after a sale)
  m(day) = revenue that day / base, for each past season (2023, 2024, 2025)
  Each day of the target season borrows m from the matching day of past seasons:
    before the sale starts ........ 1.0 (an ordinary MSRP day)
    early access (sale, before T) . same number of days before Thanksgiving (T), past days that were on sale only,
                                    3-day centred average to take out day-of-week noise
    Turkey Five + Cyber Tuesday ... same offset from Thanksgiving (T .. T+5), exact day
    rest of BFCM after T+5 ........ same offset from T where that past day was still on a sale
    Holiday sale .................. Holiday level (average m over Dec 4-30 holiday days) x that calendar date's shape
                                    (m / level), so Christmas shipping cut-offs and the Dec 31 last-day spike line up
  Past seasons are weighted by recency (weight = rank ** POW, same idea as the monthly model's holiday weighting).
  forecast(day) = base_now x weighted m.
"""
import json, os, datetime as dt
from statistics import mean

HERE = os.path.dirname(os.path.abspath(__file__))
D = dt.date.fromisoformat
POW = 6
# Amazon reported $0 sales Nov 20-26 2025 (listing appears to have been down the week before Black Friday): treated as missing
THANKS = {2023: '2023-11-23', 2024: '2024-11-28', 2025: '2025-11-27', 2026: '2026-11-26', 2027: '2027-11-25'}


def days(a, b):
    a, b = D(a), D(b)
    return [(a + dt.timedelta(i)).isoformat() for i in range((b - a).days + 1)]


def add(d, k): return (D(d) + dt.timedelta(k)).isoformat()


AMZ_OUTAGE = set(days('2025-11-20', '2025-11-26'))


def load():
    P = json.load(open(os.path.join(HERE, 'promos.json')))['events']
    shop = json.load(open(os.path.join(HERE, 'shop_daily.json')))
    ap = os.path.join(HERE, 'amazon_daily.json')
    amz = json.load(open(ap)) if os.path.exists(ap) else {}
    series = {'shop': {k: v[1] for k, v in shop.items()}, 'orders': {k: v[0] for k, v in shop.items()},
              'amz': {k: v[0] for k, v in amz.items() if k not in AMZ_OUTAGE}}
    return P, series


def season(P, y):
    """The BFCM and Holiday events for season y, plus sets of sale / near-sale days."""
    bf = next(e for e in P if e['type'] == 'bfcm' and e['end'][:4] == str(y))
    ho = next((e for e in P if e['type'] == 'holiday' and e['start'][:4] == str(y)), None)
    return bf, ho


def sale_sets(P, chan):
    on, near = set(), set()
    for e in P:
        if (e['type'], e['start']) == ('fathers', '2026-06-08'): continue
        win = days(*e['amazon']) if (chan == 'amz' and e.get('amazon')) else days(e['start'], e['end'])
        on.update(win); near.update(win); near.update(days(win[-1], add(win[-1], 7)))
    return on, near


def base(series, near, y, upto=None):
    ks = [k for k in days(f'{y}-09-01', f'{y}-10-31') if k in series and k not in near and (upto is None or k < upto)]
    return (mean(series[k] for k in ks), len(ks)) if ks else (None, 0)


def profile(P, series, y, chan, on, near):
    """m(day) for season y keyed by role: ('k', days before T), ('t', offset from T), ('h', 'MM-DD'), plus holiday level."""
    b, _ = base(series, near, y)
    if not b: return None
    T = THANKS[y]; bf, ho = season(P, y)
    m = lambda d: series[d] / b if d in series else None
    out = {'base': b, 'k': {}, 't': {}, 'h': {}, 'level': None}
    for d in days(bf['start'], add(T, -1)):
        if d in on and m(d) is not None: out['k'][(D(T) - D(d)).days] = m(d)
    for off in range(0, 12):
        d = add(T, off)
        if d in on and m(d) is not None: out['t'][off] = m(d)
    if ho:
        hd = [d for d in days(max(ho['start'], add(T, 6)), f'{y}-12-31') if m(d) is not None]
        lvl = [m(d) for d in hd if '12-04' <= d[5:] <= '12-30']
        if lvl:
            out['level'] = mean(lvl)
            raw = {d[5:]: m(d) / out['level'] for d in hd}
            for md, v in raw.items():
                if md in ('12-24', '12-25', '12-26', '12-30', '12-31'): out['h'][md] = v; continue
                nb = [raw[x] for x in raw if abs(int(x[3:]) - int(md[3:])) <= 1 and x[:2] == md[:2] and x not in ('12-24', '12-25', '12-26', '12-30', '12-31')]
                out['h'][md] = mean(nb)
    return out


def wmean(pairs):
    pairs = [(v, w) for v, w in pairs if v is not None]
    return sum(v * w for v, w in pairs) / sum(w for _, w in pairs) if pairs else None


def forecast(P, series, chan, y, hist_years, base_now):
    on, near = sale_sets(P, chan)
    prof = {hy: profile(P, series, hy, chan, on, near) for hy in hist_years}
    prof = {k: v for k, v in prof.items() if v}
    ws = {hy: (i + 1) ** POW for i, hy in enumerate(sorted(prof))}
    T = THANKS[y]; bf, ho = season(P, y)
    out = {}
    for d in days(f'{y}-11-01', f'{y}-12-31'):
        if d < bf['start']:
            role, mm = 'MSRP', 1.0
        elif d < T:
            k = (D(T) - D(d)).days
            def sm(p):
                v = [p['k'].get(j) for j in (k - 1, k, k + 1) if p['k'].get(j) is not None]
                return mean(v) if v else None
            mm = wmean([(sm(p), ws[hy]) for hy, p in prof.items()])
            if mm is None:   # earlier than any past early-access day: use the earliest week we have
                mm = wmean([(mean([p['k'][j] for j in sorted(p['k'])[-7:]]), ws[hy]) for hy, p in prof.items() if p['k']])
            role = 'Early access'
        elif d <= bf['end'] or (D(d) - D(T)).days <= 5:
            off = (D(d) - D(T)).days
            mm = wmean([(p['t'].get(off), ws[hy]) for hy, p in prof.items()])
            if mm is None: mm = wmean([(p['t'].get(5), ws[hy]) for hy, p in prof.items()])
            role = ['Thanksgiving', 'Black Friday', 'Saturday', 'Sunday', 'Cyber Monday', 'Cyber Tuesday'][off] if off <= 5 else 'BFCM (last days)'
        elif ho and d >= ho['start']:
            lvl = wmean([(p['level'], ws[hy]) for hy, p in prof.items()])
            sh = wmean([(p['h'].get(d[5:]), ws[hy]) for hy, p in prof.items()])
            mm = lvl * (sh if sh is not None else 1.0)
            role = 'Holiday sale'
        else:
            role, mm = 'MSRP', 1.0
        out[d] = dict(role=role, m=mm, v=base_now * mm)
    return out, ws


def run(y, upto=None, chans=('shop', 'orders', 'amz')):
    """Forecast season y using only seasons before y and the Sep-Oct base of year y (days before `upto`)."""
    P, S = load()
    res = {}
    for c in chans:
        if not S[c]: continue
        on, near = sale_sets(P, 'amz' if c == 'amz' else 'shop')
        b, n = base(S[c], near, y, upto)
        if not b: continue
        hist = [h for h in (2023, 2024, 2025) if h < y]
        f, ws = forecast(P, S[c], 'amz' if c == 'amz' else 'shop', y, hist, b)
        res[c] = dict(base=b, base_days=n, days=f, weights=ws)
    return res


if __name__ == '__main__':
    P, S = load()
    for y in (2024, 2025, 2026):
        r = run(y, upto=f'{y}-11-01')
        for c, v in r.items():
            for mo in ('11', '12'):
                fc = sum(x['v'] for d, x in v['days'].items() if d[5:7] == mo)
                ac = sum(S[c].get(d, 0) for d in v['days'] if d[5:7] == mo) if y < 2026 else None
                print(y, c, mo, f"base {v['base']:.0f}  fc {fc:,.0f}" + (f"  act {ac:,.0f}  miss {fc/ac-1:+.0%}" if ac else ''))


# ---------------------------------------------------------------------------------------------------------------
# Season build for the dashboard: the monthly model sets how big Nov + Dec is, the daily calendar decides when it lands.
ROLE_ORDER = ['MSRP', 'Early access', 'Thanksgiving', 'Black Friday', 'Saturday', 'Sunday', 'Cyber Monday', 'Cyber Tuesday', 'BFCM (last days)', 'Holiday sale']
AD_DAMP = 0.5     # Meta/Google spend per day follows Shopify's daily multiplier ** 0.5 (no daily spend history in the connector)


def lift_scale(days_, base_now, total):
    """Keep full-price days at base; scale only the lift above base so the season sums to `total`."""
    b = sum(base_now for _ in days_)
    lift = sum(x['v'] - base_now for x in days_.values())
    k = (total - b) / lift if lift > 0 else 1.0
    k = max(0.25, min(k, 4.0))
    return {d: base_now + k * (x['v'] - base_now) for d, x in days_.items()}, k


def build_season(DJ, y=2026):
    """DJ = data.json dict. Returns (daily payload, {month: {'orders','amazon'}} season-split monthly values)."""
    P, S = load()
    ap = os.path.join(HERE, 'amazon_daily.json')
    global AMZ_RAW
    AMZ_RAW = {k: v[0] for k, v in json.load(open(ap)).items()} if os.path.exists(ap) else {}
    months = [f'{y}-11', f'{y}-12']
    fut = {r['month']: r for r in DJ['fut'] if r['month'] in months}
    if len(fut) < 2: return None, {}
    # October bases (full-price days only), same rule for every channel
    on_s, near_s = sale_sets(P, 'shop'); on_a, near_a = sale_sets(P, 'amz')
    def oct_base(series, near):
        ks = [k for k in days(f'{y}-10-01', f'{y}-10-31') if k in series and k not in near]
        return (mean(series[k] for k in ks), len(ks)) if ks else (None, 0)
    hist = [h for h in (2023, 2024, 2025) if h < y]
    out, info = {}, {}
    # Shopify revenue (net basis = the model's d2c basis) and orders
    for c, near, on in (('shop', near_s, on_s), ('orders', near_s, on_s), ('amz', near_a, on_a)):
        if not S[c]: continue
        b, n = oct_base(S[c], near)
        if not b: continue
        f, ws = forecast(P, S[c], 'amz' if c == 'amz' else 'shop', y, hist, b)
        out[c] = dict(base=b, base_days=n, raw=f, weights=ws)
    if 'shop' not in out: return None, {}
    aov = {m: fut[m]['aov'] for m in months}
    tot_shop = sum(fut[m]['orders'] * aov[m] for m in months)            # = model d2c (net basis)
    shop_d, k_shop = lift_scale(out['shop']['raw'], out['shop']['base'], tot_shop)
    res = {}
    split = {}
    res['shop'] = shop_d
    if 'amz' in out:
        # Amazon: Seller Board sales -> Targets basis with the last 12 months' ratio
        st = json.load(open(os.path.join(HERE, 'store.json')))['months']
        ks = [m for m in sorted(st) if st[m].get('amazon') and st[m].get('sb_sales')][-12:]
        rA = sum(st[m]['amazon'] for m in ks) / sum(st[m]['sb_sales'] for m in ks)
        raw = {d: dict(x, v=x['v'] * rA) for d, x in out['amz']['raw'].items()}
        amz_d, k_amz = lift_scale(raw, out['amz']['base'] * rA, sum(fut[m]['amazon'] for m in months))
        res['amz'] = amz_d
        info['amz'] = dict(base=out['amz']['base'] * rA, k=k_amz, ratio=rA, base_days=out['amz']['base_days'])
    # orders: daily shape from orders history, scaled to the Shopify revenue split / AOV
    ordr, k_o = lift_scale(out['orders']['raw'], out['orders']['base'], sum(fut[m]['orders'] for m in months))
    for m in months:
        mshop = sum(v for d, v in shop_d.items() if d[:7] == m)
        split[m] = dict(orders=mshop / aov[m])
        if 'amz' in res: split[m]['amazon'] = sum(v for d, v in res['amz'].items() if d[:7] == m)
        mo = sum(v for d, v in ordr.items() if d[:7] == m)
        for d in ordr:
            if d[:7] == m: ordr[d] *= split[m]['orders'] / mo     # orders follow their own daily shape, month total ties to revenue / AOV
    # ad spend: Amazon ads by Amazon's daily revenue shape, Meta + Google by Shopify's (dampened); month totals kept
    rows = []
    for d in days(f'{y}-11-01', f'{y}-12-31'):
        x = out['shop']['raw'][d]
        rows.append(dict(date=d, role=x['role'], shop=round(shop_d[d], 2), amz=round(res['amz'][d], 2) if 'amz' in res else None,
                         orders=round(ordr[d], 2), m_shop=round(x['m'], 3),
                         m_amz=round(out['amz']['raw'][d]['m'], 3) if 'amz' in out else None))
    for m in months:
        R = [r for r in rows if r['date'][:7] == m]
        wg = [r['m_shop'] ** AD_DAMP for r in R]; wa = [(r['m_amz'] or 1) ** AD_DAMP for r in R]
        for r, a, b2 in zip(R, wg, wa):
            r['meta'] = round(fut[m]['meta'] * a / sum(wg), 2); r['google'] = round(fut[m]['google'] * a / sum(wg), 2)
            r['amz_ads'] = round(fut[m]['amz_ads'] * b2 / sum(wa), 2)
            r['ddo'] = round(fut[m]['ddo'] / len(R), 2)
    # last year, same weekday (364 days back) so Thanksgiving lines up
    for r in rows:
        ly = add(r['date'], -364) if r['role'] != 'Holiday sale' else f"{y-1}{r['date'][4:]}"   # BFCM: same weekday; Holiday: same date
        r['ly_date'] = ly
        r['ly_shop'] = round(S['shop'].get(ly), 2) if ly in S['shop'] else None
        ra = AMZ_RAW.get(ly)    # raw file, outage days included: last year really sold $0 on Amazon those days
        r['ly_amz'] = round(ra * info['amz']['ratio'], 2) if 'amz' in info and ra is not None else None
        r['ly_amz_outage'] = ly in AMZ_OUTAGE
        r['ly_orders'] = S['orders'].get(ly)
    T = THANKS[y]; bf, ho = season(P, y)
    payload = dict(year=y, months=months, rows=rows, thanksgiving=T, bfcm=[bf['start'], bf['end']], holiday=[ho['start'], ho['end']] if ho else None,
                   shop=dict(base=out['shop']['base'], base_days=out['shop']['base_days'], k=k_shop),
                   orders=dict(base=out['orders']['base'], k=k_o), amz=info.get('amz'),
                   weights={str(k): v for k, v in out['shop']['weights'].items()}, ad_damp=AD_DAMP)
    return payload, split
