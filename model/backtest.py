"""Out-of-sample backtest of the full dashboard stack, Jul to Sep 2026.

For each origin (1 Jul, 1 Aug, 1 Sep 2026) the model is refit on ONLY the months closed before that date
and asked to forecast every month from the origin through Sep 2026. Nothing from the forecast months is
used: seasonality, trend, AOV mix, conversion, Amazon level/slope, D/D/O level, COGS %, ad ratio,
Shopify-to-books ratio and fixed costs all come from data before the origin.

Forecast basis = what the dashboard shows (O&F Targets basis): Shopify = model net sales x fin_ratio.
Fixed costs: the live model reads the finance sheet's budget for future months; that budget as it stood on
each origin date is not archived, so the backtest uses the mean of the last 3 closed months instead.

Benchmarks: the O&F plan (Targets 'Est.' columns) and a naive 'same month last year x trailing-3-month YoY'.
Usage: python3 backtest.py  ->  prints a table and writes backtest_out.json
"""
import json, statistics as st
from data import *
import model as M

ORIGINS = ['2026-07', '2026-08', '2026-09']
TARGETS = ['2026-07', '2026-08', '2026-09']
FIELDS = ['shop', 'amz', 'ddo', 'total', 'ni']


def forecast_from(origin):
    cut = MONTHS.index(origin)                    # fit on MONTHS[:cut], i.e. through the month before origin
    f = M.fit(cut)
    H = f['horizon']                              # origin .. last closed month
    l12 = range(cut - 12, cut)
    fin_ratio = sum(TGT['shop'][i] for i in l12) / sum(NET[i] for i in l12)
    amz_f, _, _ = M.project_amazon(AMAZON, cut, H)
    meta_f, _, _, _ = M.project_sparse(META, H, f['s_ord'], cut=cut)
    gg_f, _, _, _ = M.project_sparse(GOOGLE, H, f['s_ord'], cut=cut)
    aa_f, _, _ = M.project_amazon(AMZ_ADS, cut, H)
    ddo_level = st.mean(DDO[i] for i in range(cut) if MONTHS[i] >= '2026-01' and DDO[i] is not None)
    cogs_pct = sum(TGT['cogs'][i] for i in l12) / sum(TGT['total'][i] for i in l12)
    ads = lambda i: (META[i] or 0) + (GOOGLE[i] or 0) + (AMZ_ADS[i] or 0)
    ad_ratio = sum(TGT['ad'][i] for i in l12) / sum(ads(i) for i in l12)
    fixed = st.mean(month_field(MONTHS[i], 'fixed') for i in range(cut - 3, cut))
    out = {}
    for h, ym in enumerate(H):
        shop = f['d2c_f'][h] * fin_ratio
        tot = shop + amz_f[h] + ddo_level
        ad = (meta_f[h] + gg_f[h] + aa_f[h]) * ad_ratio
        out[ym] = dict(shop=shop, amz=amz_f[h], ddo=ddo_level, total=tot, ni=tot * (1 - cogs_pct) - ad - fixed,
                       orders=f['orders_f'][h], aov=f['aov_f'][h], ad=ad, fixed=fixed, cogs_pct=cogs_pct)
    return out


def actual(ym):
    i = MONTHS.index(ym)
    return dict(shop=TGT['shop'][i], amz=TGT['amz'][i], ddo=TGT['ddo'][i], total=TGT['total'][i], ni=TGT['ni'][i],
                orders=ORDERS[i], aov=NET[i] / ORDERS[i], ad=TGT['ad'][i], fixed=month_field(ym, 'fixed'),
                cogs_pct=TGT['cogs'][i] / TGT['total'][i])


def plan(ym):
    g = {k: month_field(ym, 'goal_' + k) for k in ('shop', 'amz', 'ddo', 'ni')}
    g['total'] = g['shop'] + g['amz'] + g['ddo']
    return g


def naive(ym):
    """Same month last year x trailing-3-month YoY, using only months before the forecast origin."""
    return None


def main():
    res = {o: forecast_from(o) for o in ORIGINS}
    rows = []
    for o in ORIGINS:
        cut = MONTHS.index(o)
        for ym in TARGETS:
            if ym < o: continue
            a, fc, p = actual(ym), res[o][ym], plan(ym)
            i = MONTHS.index(ym)
            # naive: last year's same month scaled by YoY of the last 3 closed months before the origin
            nv = {}
            for k, ser in (('shop', TGT['shop']), ('amz', TGT['amz'])):
                yoy = sum(ser[j] for j in range(cut - 3, cut)) / sum(ser[j - 12] for j in range(cut - 3, cut))
                nv[k] = ser[i - 12] * yoy
            rows.append(dict(origin=o, month=ym, ahead=MONTHS.index(ym) - cut + 1, actual=a, forecast=fc, plan=p, naive=nv))
    json.dump(rows, open('backtest_out.json', 'w'), indent=1, default=float)

    k = lambda v: f"{v/1000:>7.1f}k"
    for r in rows:
        a, fc, p = r['actual'], r['forecast'], r['plan']
        print(f"\nfrom {r['origin']} -> {r['month']} ({r['ahead']} mo ahead)")
        for fld in FIELDS:
            err = (fc[fld] - a[fld]) / abs(a[fld]) if a[fld] else 0
            print(f"  {fld:5} fc {k(fc[fld])} act {k(a[fld])} plan {k(p[fld])}  miss {fc[fld]-a[fld]:+9,.0f} ({err:+.0%})")
        print(f"  drivers: orders fc {fc['orders']:.0f} act {a['orders']}  aov fc {fc['aov']:.0f} act {a['aov']:.0f}  "
              f"ads fc {fc['ad']:,.0f} act {a['ad']:,}  fixed fc {fc['fixed']:,.0f} act {a['fixed']:,}  cogs fc {fc['cogs_pct']:.1%} act {a['cogs_pct']:.1%}")
        print(f"  naive shop {k(r['naive']['shop'])} amz {k(r['naive']['amz'])}")
    # summary: 1-month-ahead and the Q3 total from 1 Jul
    print('\nSUMMARY')
    for ah in (1, 2, 3):
        rr = [r for r in rows if r['ahead'] == ah]
        for fld in ('shop', 'amz', 'total'):
            m = st.mean(abs(r['forecast'][fld] / r['actual'][fld] - 1) for r in rr)
            pm = st.mean(abs(r['plan'][fld] / r['actual'][fld] - 1) for r in rr)
            print(f"  {ah} mo ahead {fld:5} model MAPE {m:.0%}  plan MAPE {pm:.0%}  (n={len(rr)})")
    q = [r for r in rows if r['origin'] == '2026-07']
    for fld in FIELDS:
        fc = sum(r['forecast'][fld] for r in q); ac = sum(r['actual'][fld] for r in q); pl = sum(r['plan'][fld] for r in q)
        print(f"  Q3 from 1 Jul {fld:5} model {fc:>10,.0f} actual {ac:>10,.0f} plan {pl:>10,.0f}  model miss {fc/ac-1 if ac>0 else float('nan'):+.1%}  plan miss {pl/ac-1 if ac>0 else float('nan'):+.1%}")


if __name__ == '__main__':
    main()
