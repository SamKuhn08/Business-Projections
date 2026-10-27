"""Sale lift per promotion: revenue during the sale vs the same number of days right before it (evergreen).

Descriptive only, nothing here feeds the forecast. Sam's definition (2026-10-27):
  sale revenue      = Shopify + Amazon revenue over the sale days
  evergreen revenue = Shopify + Amazon revenue over the same number of days immediately before the sale
  multiplier        = sale revenue / evergreen revenue
Shopify = net sales by day (shop_daily.json). Amazon = Seller Board sales (amazon_sale_windows.json); when the
Amazon Prime days differ from the Shopify sale dates (Prime Day 2024, October Prime 2025) each channel uses its own
sale days and its own matching prior window.
Included: documented sales from 2024 on that have finished. Excluded: Father's Day 2026 (gift with purchase, not a
discount; Sam 2026-10-27) and 2023 (dates only inferred, no promo doc).
Flag 'prior_sale': at least a quarter of the prior days were another sale, so evergreen is not MSRP; those are left out
of totals by default (BFCM's lookback catches 2-4 October Prime days and is NOT flagged).
"""
import json, os, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = {'presidents': "Presidents' Day", 'spring': 'Spring sale', 'memorial': 'Memorial Day', 'fathers': "Father's Day",
         'july4': 'July 4th', 'prime_summer': 'Prime Day', 'labor': 'Labor Day', 'prime_oct': 'October Prime',
         'bfcm': 'Black Friday / Cyber Monday', 'holiday': 'Holiday'}
EXCLUDE = {('fathers', '2026-06-08')}
D = dt.date.fromisoformat


def days(a, b):
    a, b = D(a), D(b)
    return [(a + dt.timedelta(i)).isoformat() for i in range((b - a).days + 1)]


def prior(a, b):
    n = (D(b) - D(a)).days + 1
    return (D(a) - dt.timedelta(n)).isoformat(), (D(a) - dt.timedelta(1)).isoformat()


def build(today=None):
    P = json.load(open(os.path.join(HERE, 'promos.json')))['events']
    shop = json.load(open(os.path.join(HERE, 'shop_daily.json')))
    ap = os.path.join(HERE, 'amazon_sale_windows.json')
    amz = {(r['start'], r['end']): r for r in (json.load(open(ap)) if os.path.exists(ap) else [])}
    last_shop = max(shop)
    sale_days = set()
    for e in P:
        if e['type'] == 'fathers' and e['start'] == '2026-06-08': continue
        sale_days.update(days(e['start'], e['end']))
        if e.get('amazon'): sale_days.update(days(*e['amazon']))
    out = []
    for e in P:
        if e['start'] < '2024-01-01' or e.get('assumed') or (e['type'], e['start']) in EXCLUDE: continue
        if e['end'] > last_shop: continue
        ps, pe = prior(e['start'], e['end'])
        s_sale = sum(shop.get(d, [0, 0])[1] for d in days(e['start'], e['end']))
        s_prior = sum(shop.get(d, [0, 0])[1] for d in days(ps, pe))
        aw = tuple(e['amazon']) if e.get('amazon') else (e['start'], e['end'])
        a = amz.get(aw)
        own = set(days(e['start'], e['end'])) | (set(days(*e['amazon'])) if e.get('amazon') else set())
        pdays = set(days(ps, pe)) | (set(days(a['prior_start'], a['prior_end'])) if a else set())
        overlap = sorted(pdays & (sale_days - own))
        r = dict(type=e['type'], name=NAMES.get(e['type'], e['type']), note=e.get('note'), start=e['start'], end=e['end'],
                 days=len(days(e['start'], e['end'])), pro=e.get('pro'), prior_start=ps, prior_end=pe,
                 shop_sale=round(s_sale), shop_prior=round(s_prior),
                 amz_sale=round(a['sales']) if a else None, amz_prior=round(a['prior_sales']) if a else None,
                 amz_start=aw[0], amz_end=aw[1], prior_sale=len(overlap) >= 0.25 * len(pdays), overlap_days=len(overlap), prior_days=len(pdays))
        r['sale'] = r['shop_sale'] + (r['amz_sale'] or 0)
        r['evergreen'] = r['shop_prior'] + (r['amz_prior'] or 0)
        r['mult'] = round(r['sale'] / r['evergreen'], 3) if r['evergreen'] else None
        r['amz_missing'] = a is None
        out.append(r)
    return out


if __name__ == '__main__':
    for r in build():
        print(f"{r['start']} {r['name']:28} {r['days']:3}d  sale {r['sale']:>9,}  evergreen {r['evergreen']:>9,}  x{r['mult']}"
              f"  shop x{r['shop_sale']/max(r['shop_prior'],1):.2f}" + ('  PRIOR OVERLAPS SALE' if r['prior_sale'] else '') + ('  (no Amazon)' if r['amz_missing'] else ''))
