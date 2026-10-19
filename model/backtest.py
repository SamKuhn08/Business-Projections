"""Out-of-sample backtest of the full dashboard stack against actuals (no plan, no O&F forecast anywhere).

For each origin the model is refit on ONLY the months closed before that date and forecasts every month from
the origin up to the last closed month. Nothing from the forecast months is used: seasonality, trend, AOV mix,
promo lifts (only promos that had finished), Amazon Prime lifts, the Shopify bias correction, D/D/O level,
COGS %, ad ratio, Shopify-to-books ratio and fixed costs all come from data before the origin.
Basis = what the dashboard shows (O&F Targets actuals): Shopify = model net sales x fin_ratio.

Benchmark ('naive'): same month last year x the year-over-year change of the last 3 closed months, per channel.
It needs no model at all, so the model has to beat it to be worth anything.

Usage: python3 backtest.py  ->  prints tables, writes backtest_out.json (also read by refresh.py for the dashboard)
"""
import json, math, statistics as st
from data import *
import model as M
import events as EV

FIELDS = ['shop', 'amz', 'ddo', 'total', 'ni']


def forecast_from(origin):
    cut = MONTHS.index(origin)
    f = M.apply_bias(dict(M.fit(cut)), M.shop_bias(cut))
    H = f['horizon']
    l12 = range(cut - 12, cut)
    fin_ratio = sum(TGT['shop'][i] for i in l12) / sum(NET[i] for i in l12)
    amz_f, _, _, _ = M.project_amazon_events(cut, H)
    meta_f, _, _, _ = M.project_sparse(META, H, f['s_ord'], cut=cut)
    gg_f, _, _, _ = M.project_sparse(GOOGLE, H, f['s_ord'], cut=cut)
    aa_f, _, _ = M.project_amazon(AMZ_ADS, cut, H)
    ddo_level = st.mean(DDO[i] for i in range(cut) if MONTHS[i] >= '2026-01' and DDO[i] is not None) \
        if any(MONTHS[i] >= '2026-01' for i in range(cut)) else st.mean(DDO[i] for i in range(cut - 6, cut))
    cogs_pct = sum(TGT['cogs'][i] for i in l12) / sum(TGT['total'][i] for i in l12)
    ads = lambda i: (META[i] or 0) + (GOOGLE[i] or 0) + (AMZ_ADS[i] or 0)
    ad_ratio = sum(TGT['ad'][i] for i in l12) / sum(ads(i) for i in l12)
    fx = [TGT['total'][i] - TGT['cogs'][i] - TGT['ad'][i] - TGT['ni'][i] for i in range(cut - 3, cut)]
    fixed = st.mean(fx)
    out = {}
    for h, ym in enumerate(H):
        shop = f['d2c_f'][h] * fin_ratio
        tot = shop + amz_f[h] + ddo_level
        ad = (meta_f[h] + gg_f[h] + aa_f[h]) * ad_ratio
        out[ym] = dict(shop=shop, amz=amz_f[h], ddo=ddo_level, total=tot, ni=tot * (1 - cogs_pct) - ad - fixed,
                       orders=f['orders_f'][h], aov=f['d2c_f'][h] / f['orders_f'][h], ad=ad, fixed=fixed, cogs_pct=cogs_pct,
                       promos=f['promo_ev'][h], promo_shop=f['promo_r'][h] * fin_ratio)
    return out


def actual(ym):
    i = MONTHS.index(ym)
    return dict(shop=TGT['shop'][i], amz=TGT['amz'][i], ddo=TGT['ddo'][i], total=TGT['total'][i], ni=TGT['ni'][i],
                orders=ORDERS[i], aov=NET[i] / ORDERS[i], ad=TGT['ad'][i],
                fixed=TGT['total'][i] - TGT['cogs'][i] - TGT['ad'][i] - TGT['ni'][i], cogs_pct=TGT['cogs'][i] / TGT['total'][i])


def naive(ym, origin):
    """Same month last year x YoY of the last 3 closed months before the origin, per channel."""
    cut, i = MONTHS.index(origin), MONTHS.index(ym)
    out = {}
    for k in ('shop', 'amz', 'ddo'):
        ser = TGT[k]
        yoy = sum(ser[j] for j in range(cut - 3, cut)) / sum(ser[j - 12] for j in range(cut - 3, cut))
        out[k] = ser[i - 12] * yoy
    out['total'] = out['shop'] + out['amz'] + out['ddo']
    return out


def rows_for(origins, last=None):
    rows = []
    last = last or MONTHS[CUT - 1]
    for o in origins:
        fc = forecast_from(o)
        for ym, v in fc.items():
            if ym > last: continue
            rows.append(dict(origin=o, month=ym, ahead=MONTHS.index(ym) - MONTHS.index(o) + 1,
                             forecast=v, actual=actual(ym), naive=naive(ym, o)))
    return rows


def summary(rows, skip=('2025-01', '2025-02')):
    out = {}
    for fld in ('shop', 'amz', 'total'):
        for ah in (1, 2, 3):
            rr = [r for r in rows if r['ahead'] == ah and r['month'] not in skip]
            if not rr: continue
            m = [r['forecast'][fld] / r['actual'][fld] - 1 for r in rr]
            n = [r['naive'][fld] / r['actual'][fld] - 1 for r in rr]
            out[f'{fld}_{ah}'] = dict(n=len(rr), model_mape=st.mean(abs(x) for x in m), model_bias=st.mean(m),
                                      naive_mape=st.mean(abs(x) for x in n), naive_bias=st.mean(n))
    return out


def main():
    q3 = rows_for(['2026-07', '2026-08', '2026-09'])
    broad = rows_for([m for m in MONTHS if '2025-01' <= m <= MONTHS[CUT - 1]])
    S = summary(broad)
    k = lambda v: f"{v/1000:>6.0f}k"
    print('Q3 2026 backtest (Targets basis)')
    for r in q3:
        a, fc, nv = r['actual'], r['forecast'], r['naive']
        print(f"  from {r['origin']} -> {r['month']} ({r['ahead']} ahead)  total fc {k(fc['total'])} act {k(a['total'])} ({fc['total']/a['total']-1:+.0%}) naive {k(nv['total'])} ({nv['total']/a['total']-1:+.0%}) | "
              f"shop {k(fc['shop'])}/{k(a['shop'])} ({fc['shop']/a['shop']-1:+.0%})  amz {k(fc['amz'])}/{k(a['amz'])} ({fc['amz']/a['amz']-1:+.0%})  ddo {k(fc['ddo'])}/{k(a['ddo'])}  NI {fc['ni']:>8,.0f} / {a['ni']:>8,.0f}  promos {fc['promos']}")
    q = [r for r in q3 if r['origin'] == '2026-07']
    for fld in ('shop', 'amz', 'ddo', 'total', 'ni'):
        fc = sum(r['forecast'][fld] for r in q); ac = sum(r['actual'][fld] for r in q)
        nv = sum(r['naive'][fld] for r in q) if fld in ('shop', 'amz', 'ddo', 'total') else None
        print(f"  Q3 from 1 Jul {fld:5} model {fc:>10,.0f} actual {ac:>10,.0f} miss {fc-ac:+10,.0f}" + (f"  naive {nv:>10,.0f} miss {nv-ac:+10,.0f}" if nv else ''))
    print(f"\nBroad backtest: every monthly origin {broad[0]['origin']} .. {broad[-1]['origin']}, 1-3 months ahead (Rogan months skipped)")
    for key, v in S.items():
        print(f"  {key:8} n={v['n']:2}  model {v['model_mape']:.0%} (bias {v['model_bias']:+.0%})   naive {v['naive_mape']:.0%} (bias {v['naive_bias']:+.0%})")
    json.dump(dict(q3=q3, broad=broad, summary=S), open('backtest_out.json', 'w'), indent=1, default=float)


if __name__ == '__main__':
    main()
