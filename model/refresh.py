"""Merge freshly pulled raw files into store.json, rerun the model, write site/public/data.json.

Usage:  python3 refresh.py RAW_DIR TODAY(YYYY-MM-DD)
RAW_DIR holds the files described in REFRESH.md. Any file may be missing; missing pieces leave
the stored values untouched. Prints a validation report and exits non-zero if something looks wrong.
"""
import json, os, sys, subprocess, calendar

HERE = os.path.dirname(os.path.abspath(__file__))
IN_TYPES = {'Iron Necks', '', 'Closeout Sale'}
HA_TYPES = {'Neck Harnesses', 'Accessories', 'Shoulder Bands'}
LP_MAP = {'Product': 'P', 'Collection': 'C', 'Homepage': 'H', 'Custom Page': 'L', 'Blog Article': 'B'}


def load(raw, name):
    p = os.path.join(raw, name)
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    rows = d['rows'] if isinstance(d, dict) and 'rows' in d else d
    # ShopifyQL via GraphQL (shopifyqlQuery.tableData.rows) gives dicts in column order; run-analytics-query gives lists
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        rows = [['' if v is None else v for v in r.values()] for r in rows]   # null product_type = '' as before
    return rows


def ym(s): return s[:7]


def main(raw, today):
    store_p = os.path.join(HERE, 'store.json')
    store = json.load(open(store_p))
    M = store['months']
    problems, touched = [], set()

    def rec(m):
        r = M.setdefault(m, {})
        r.setdefault('lp', {}); r.setdefault('ref', {})
        return r

    rows = load(raw, 'totals.json')          # [month, orders, gross, discounts, net, total, aov]
    if rows:
        for r in rows:
            e = rec(ym(r[0])); e['orders'] = int(r[1]); e['net'] = round(float(r[4])); touched.add(ym(r[0]))
    rows = load(raw, 'sessions.json')        # [month, sessions, cart, completed, cr]
    if rows:
        for r in rows:
            e = rec(ym(r[0])); e['sessions'] = int(r[1]); e['checkouts'] = int(r[3])
    rows = load(raw, 'landing.json')         # [month, landing_page_type, sessions, completed]
    if rows:
        acc = {}
        for r in rows:
            k = LP_MAP.get(r[1])
            if not k: continue
            acc.setdefault(ym(r[0]), {})[k] = [int(r[2]), int(r[3])]
        for m, v in acc.items():
            e = rec(m)
            for k in 'PCHLB':
                e['lp'][k] = v.get(k, [0, 0])
    rows = load(raw, 'referrer.json')        # [month, referrer_source, sessions, completed]
    if rows:
        acc = {}
        for r in rows:
            if r[1] in ('search', 'direct', 'social'):
                acc.setdefault(ym(r[0]), {})[r[1]] = [int(r[2]), int(r[3])]
        for m, v in acc.items():
            e = rec(m)
            for k in ('search', 'direct', 'social'):
                e['ref'][k] = v.get(k, [0, 0])
    rows = load(raw, 'product.json')         # [month, product_type, net_sales, quantity]
    if rows:
        acc = {}
        for r in rows:
            a = acc.setdefault(ym(r[0]), [0.0, 0.0])
            if r[1] in IN_TYPES: a[0] += float(r[2])
            elif r[1] in HA_TYPES: a[1] += float(r[2])
        for m, (i, h) in acc.items():
            e = rec(m); e['line_in'] = round(i); e['line_ha'] = round(h)
    k = load(raw, 'klaviyo.json')            # {"months": [...], "email_rev": [...], "signups": [...]}
    if k:
        for i, m in enumerate(k['months']):
            e = rec(m)
            if k.get('email_rev') is not None: e['email_rev'] = round(k['email_rev'][i])
            if k.get('signups') is not None: e['signups'] = int(k['signups'][i])
    fin = load(raw, 'finance.json')          # {"YYYY-MM": {sheet_shop, amazon, meta, google, amz_ads, fixed, goal_shop, goal_amz}}
    if fin:
        for m, v in fin.items():
            e = rec(m)
            for kk, vv in v.items():
                if vv is None: continue
                # Targets tab owns these: never let the finance sheet overwrite them
                if kk == 'amazon' and e.get('tgt_amz') is not None: continue
                if kk in ('goal_shop', 'goal_amz') and e.get('goal_ddo') is not None: continue
                e[kk] = vv

    tg = load(raw, 'targets.json')           # {"YYYY-MM": {"tgt_shop":..,"tgt_amz":..,"tgt_ddo":..,"tgt_total":..,"tgt_cogs":..,"tgt_ad":..,
    if tg:                                   #   "tgt_other_sma":..,"tgt_payroll":..,"tgt_ga":..,"tgt_other":..,"tgt_ni":..}} closed months only
        for m, v in tg.items():
            e = rec(m)
            for kk, vv in v.items():
                if vv is not None: e[kk] = vv
            if v.get('tgt_amz') is not None: e['amazon'] = v['tgt_amz']     # Targets AMZ is the Amazon basis
    pl = load(raw, 'plan.json')              # {"YYYY-MM": {"goal_shop","goal_amz","goal_ddo","goal_ni"}} from the Targets 'Est.' columns
    if pl:
        for m, v in pl.items(): rec(m).update({k: vv for k, vv in v.items() if vv is not None})
    br = load(raw, 'brand.json')             # {"YYYY-MM": {"gsc_brand_clicks","gsc_brand_impr","gsc_days"}} (manual GSC export)
    if br:
        for m, v in br.items(): rec(m).update(v)
    sd = load(raw, 'shop_daily.json')        # [day, orders, net_sales] from 'FROM sales SHOW orders, net_sales TIMESERIES day'
    if sd:
        dp = os.path.join(HERE, 'shop_daily.json')
        daily = json.load(open(dp))
        for r in sd:
            if r[0] < today: daily[r[0]] = [int(r[1]), float(r[2])]     # closed days only
        json.dump(dict(sorted(daily.items())), open(dp, 'w'))
    ad = load(raw, 'amazon_daily.json')     # {"YYYY-MM-DD": [sales, orders, units, ad_spend]} Seller Board, closed days
    if ad:
        p = os.path.join(HERE, 'amazon_daily.json'); cur = json.load(open(p)) if os.path.exists(p) else {}
        for k, v in ad.items():
            if k < today: cur[k] = v
        json.dump(dict(sorted(cur.items())), open(p, 'w'))
    sw = load(raw, 'amazon_sale_windows.json')   # [{start,end,prior_start,prior_end,sales,prior_sales}] for newly finished sales
    if sw:
        p = os.path.join(HERE, 'amazon_sale_windows.json'); cur = json.load(open(p))
        have = {(r['start'], r['end']) for r in cur}
        cur += [r for r in sw if (r['start'], r['end']) not in have]
        json.dump(cur, open(p, 'w'), indent=1)
    ay = load(raw, 'amazon_years.json')      # {"YYYY": {"sales":..,"extra_sale":[]}} after a year closes
    if ay:
        p = os.path.join(HERE, 'amazon_years.json'); cur = json.load(open(p)); cur.update(ay); json.dump(cur, open(p, 'w'), indent=1)
    mtd = load(raw, 'mtd.json')              # {"month","through","days","shop","amazon","meta","google","amz_ads"} closed days of the month in progress
    if mtd: store['meta']['mtd'] = mtd

    # ---- partial month bookkeeping
    cur = today[:7]
    store['meta']['partial_month'] = cur
    store['meta']['mtd_days'] = int(today[8:10])
    store['meta']['generated'] = today

    # ---- validation of every month that has Shopify data
    req = ['orders', 'net', 'sessions', 'checkouts', 'line_in', 'line_ha', 'email_rev', 'signups']
    for m, e in sorted(M.items()):
        if e.get('orders') is None: continue
        miss = [x for x in req if e.get(x) is None] + [f'lp.{x}' for x in 'PCHLB' if x not in e['lp']] + \
               [f'ref.{x}' for x in ('search', 'direct', 'social') if x not in e['ref']]
        if miss: problems.append(f'{m}: missing {miss}')
        if e.get('net') and e.get('orders') and not (120 < e['net'] / e['orders'] < 600):
            problems.append(f'{m}: AOV {e["net"]/e["orders"]:.0f} out of range')
        if e.get('line_in') is not None and e.get('net') and not (0.6 < (e['line_in'] + e['line_ha']) / e['net'] < 1.3):
            problems.append(f'{m}: product lines cover {(e["line_in"]+e["line_ha"])/e["net"]:.2f} of net sales')
    json.dump(store, open(store_p, 'w'), indent=1)

    out = os.path.join(HERE, '..', 'site', 'public', 'data.json')
    env = dict(os.environ, OUT=out)
    run = subprocess.run([sys.executable, 'model.py'], cwd=HERE, env=env, capture_output=True, text=True)
    print(run.stdout[-3000:])
    if run.returncode:
        print(run.stderr); problems.append('model run failed')
    # ---- track record: out-of-sample backtest of the whole stack, shown on the Method & accuracy tab
    bt = subprocess.run([sys.executable, 'backtest.py'], cwd=HERE, capture_output=True, text=True)
    print(bt.stdout[-2500:])
    if bt.returncode:
        print(bt.stderr); problems.append('backtest failed')
    else:
        B = json.load(open(os.path.join(HERE, 'backtest_out.json')))
        keep = lambda d: {k: round(d[k]) for k in ('shop', 'amz', 'ddo', 'total', 'ni') if k in d}
        slim = lambda r: dict(origin=r['origin'], month=r['month'], ahead=r['ahead'], forecast=keep(r['forecast']),
                              actual=keep(r['actual']), naive=keep(r['naive']), promos=r['forecast'].get('promos', []))
        D = json.load(open(out))
        D['track'] = dict(q3=[slim(r) for r in B['q3']], broad=[slim(r) for r in B['broad']], summary=B['summary'])
        json.dump(D, open(out, 'w'), default=float)
    # ---- Nov/Dec day by day: the monthly model sets the season size, the daily calendar moves it between Nov and Dec
    try:
        import daily
        D = json.load(open(out))
        p, split = daily.build_season(D, int(today[:4]))
        if p and today >= p['months'][0] + '-01':
            split = {}     # season under way: keep the model's months (nowcast has the actuals); daily view still shown
        if p:
            p['model_months'] = {r['month']: dict(orders=r['orders'], amazon=r['amazon'], d2c=r['d2c']) for r in D['fut'] if r['month'] in split}
            for r in D['fut']:
                if r['month'] in split:
                    r['orders'] = split[r['month']]['orders']; r['d2c'] = r['orders'] * r['aov']
                    if 'amazon' in split[r['month']]: r['amazon'] = split[r['month']]['amazon']
            D['daily'] = p
            json.dump(D, open(out, 'w'), default=float)
    except Exception as ex:
        problems.append(f'daily season build failed: {ex}')
    # ---- sale lift per promo (descriptive, Drivers tab): Shopify daily + Amazon windows from Seller Board
    try:
        import sale_lift
        D = json.load(open(out)); D['sales'] = sale_lift.build(); json.dump(D, open(out, 'w'), default=float)
        if any(r['amz_missing'] for r in D['sales']): problems.append('sale lift: a finished sale has no Amazon window in amazon_sale_windows.json')
    except Exception as ex:
        problems.append(f'sale lift failed: {ex}')
    # ---- projection history: record today's base-case projection, rebuild history.json (Projection history tab)
    if not problems:
        try:
            import projection_log
            projection_log.record(json.load(open(out)), today, note='daily refresh')
            projection_log.write(today)
        except Exception as ex:
            problems.append(f'projection history failed: {ex}')
    print('touched months:', sorted(touched))
    print('PROBLEMS:' if problems else 'OK: no validation problems', *problems, sep='\n  ')
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
