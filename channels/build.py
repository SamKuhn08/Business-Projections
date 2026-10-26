"""Channel performance data for the dashboard's Channels tab (Shopify website only).

Inputs (this folder):
  orders.json        per-order: c createdAt (UTC), a subtotal after discounts/refunds, u customer {i id, o lifetime orders,
                     e {m email marketing state}}, j customer journey {d days to conversion, f first visit, l last visit}
                     (pulled from the Shopify Admin GraphQL API, see REFRESH notes)
  sessions_raw.json  [month, referrer_source, utm_source, utm_medium, sessions, completed_checkouts] (ShopifyQL)
  blog_sessions.json [month, referrer_source, sessions, completed_checkouts] for Blog Article landings
Spend comes from ../model/store.json (meta, google). Affiliate cost = commission rate x affiliate revenue (estimate).
Output: ../site/public/channels.json
"""
import json, os, re, datetime as dt, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CH = ['meta', 'google', 'org_search', 'org_social', 'email', 'sms', 'affiliate', 'ai', 'direct']
AFF_COMMISSION = 0.15          # Social Snowball default program pays 15% (campaign names '-15c10d' = 15% commission, 10% discount)
SPAM = {'trafficheap.cc', 'wake-up-network.com', 'shopifybooster.pro', 'consentik.com'}
META_SRC = {'facebook', 'fb', 'ig', 'instagram', 'an', 'th', 'meta', 'facebook-sitelink', 'igshopping'}
AI_SRC = ('chatgpt', 'perplexity', 'gemini', 'copilot', 'claude', 'openai', 'bard')
SEARCH_NAMES = ('google', 'bing', 'duckduckgo', 'yahoo', 'ecosia', 'brave', 'baidu', 'yandex', 'aol', 'startpage')
SOCIAL_NAMES = ('instagram', 'facebook', 'tiktok', 'reddit', 'youtube', 'pinterest', 'twitter', 'x.com', 't.co', 'threads',
                'linkedin', 'snapchat', 'fb.', 'lnkd', 'discord')
EMAIL_NAMES = ('mail.', 'outlook', 'klaviyo', 'gmail', 'yahoo mail')


def by_utm(src, med):
    """Channel from UTM tags, or None if the tags do not decide it."""
    s, m = (src or '').strip().lower(), (med or '').strip().lower()
    if not s and not m: return None
    if s == 'snowball' and 'whitelisting' in m: return 'meta'          # creator-handle ads run from the Meta account
    if s in ('snowball', 'refersion', 'shop_truemed', 'govx') or 'affiliate' in m: return 'affiliate'
    if s == 'attentive' or m == 'sms': return 'sms'
    if s in ('klaviyo', 'hs_email', 'email') or m in ('email', 'email marketing', 'message'): return 'email'
    if any(a in s for a in AI_SRC): return 'ai'
    if s in ('google', 'adwords') and m in ('paid', 'cpc', 'ppc'): return 'google'
    if s == 'google' and m == 'product_sync': return 'org_search'      # free Google Shopping listings
    if s in META_SRC and m in ('paid', 'paid_social', 'cpc'): return 'meta'
    if s == '' and m == 'paid': return None                             # resolved by referrer
    if s in META_SRC or s in ('reddit', 'youtube', 'yotpo', 'tiktok', 'linktree'): return 'org_social'
    if m in ('social', 'organic', 'referral', 'feed'): return 'org_social' if s not in ('',) else None
    return 'affiliate'                                                  # other tagged partners (Shop app, Axon, etc.)


def session_channel(ref, src, med):
    if (src or '').lower() in SPAM: return None
    c = by_utm(src, med)
    if c: return c
    ref = (ref or '').lower()
    if (med or '').lower() == 'paid': return 'meta' if ref == 'social' else 'google' if ref == 'search' else 'meta'
    return {'search': 'org_search', 'social': 'org_social', 'email': 'email'}.get(ref, 'direct')


def visit_channel(v):
    if not v: return None
    u = v.get('u') or {}
    c = by_utm(u.get('s'), u.get('m'))
    if c: return c
    if (u.get('m') or '').lower() == 'paid': return 'meta'
    s = (v.get('s') or '').lower()
    if s in ('', 'direct', 'an unknown source'): return 'direct'
    if any(a in s for a in AI_SRC): return 'ai'
    if any(n in s for n in EMAIL_NAMES): return 'email'
    if any(n in s for n in SOCIAL_NAMES): return 'org_social'
    if any(n in s for n in SEARCH_NAMES): return 'org_search'
    return 'affiliate'                                                  # other referring sites (GovX, partners, press)


def local_month(iso):
    t = dt.datetime.fromisoformat(iso.replace('Z', '+00:00')) - dt.timedelta(hours=5)   # Central (close enough for monthly buckets)
    return t.strftime('%Y-%m')


def main():
    orders = json.load(open(os.path.join(HERE, 'orders.json')))
    orders.sort(key=lambda o: o['c'])
    store = json.load(open(os.path.join(HERE, '..', 'model', 'store.json')))['months']
    # order index per customer: lifetime count minus later orders we hold
    later = defaultdict(int)
    idx = [None] * len(orders)
    for k in range(len(orders) - 1, -1, -1):
        cu = orders[k].get('u')
        if cu and cu.get('i'):
            idx[k] = int(cu.get('o') or 1) - later[cu['i']]
            later[cu['i']] += 1
    # customer value: first order in our data that is the customer's first ever order -> acquisition channel per model
    agg = {}   # (model, month, ch) -> dict
    def A(model, m, ch):
        return agg.setdefault((model, m, ch), dict(orders=0, rev=0.0, new=0, new_rev=0.0, sub=0, known=0, d=[0, 0, 0, 0], dn=0, dsum=0,
                                                    cust_rev=0.0, cust_n=0, repeat=0))
    first_of = {}   # customer -> (k, ch_first, ch_last)
    for k, o in enumerate(orders):
        j = o.get('j') or {}
        m = local_month(o['c'])
        rev = float(((o.get('a') or {}).get('s') or {}).get('a') or 0)
        email = ((o.get('u') or {}).get('e') or {}).get('m')
        for model, v in (('first', j.get('f')), ('last', j.get('l'))):
            ch = visit_channel(v) or 'none'
            r = A(model, m, ch)
            r['orders'] += 1; r['rev'] += rev
            new = idx[k] == 1
            if new: r['new'] += 1; r['new_rev'] += rev
            if email: r['known'] += 1; r['sub'] += email == 'SUBSCRIBED'
            d = j.get('d')
            if d is not None:
                r['dn'] += 1; r['dsum'] += d
                r['d'][0 if d <= 1 else 1 if d <= 7 else 2 if d <= 30 else 3] += 1
        cu = (o.get('u') or {}).get('i')
        if cu and idx[k] == 1 and cu not in first_of:
            first_of[cu] = (m, visit_channel(j.get('f')) or 'none', visit_channel(j.get('l')) or 'none')
    # customer value to date for customers acquired in our window
    cust_tot = defaultdict(float); cust_n = defaultdict(int)
    for o in orders:
        cu = (o.get('u') or {}).get('i')
        if cu in first_of:
            cust_tot[cu] += float(((o.get('a') or {}).get('s') or {}).get('a') or 0); cust_n[cu] += 1
    for cu, (m, cf, cl) in first_of.items():
        for model, ch in (('first', cf), ('last', cl)):
            r = A(model, m, ch)
            r['cust_n'] += 1; r['cust_rev'] += cust_tot[cu]; r['repeat'] += cust_n[cu] > 1
    # sessions (blog landings split out of their referrer's no-UTM bucket)
    ses = defaultdict(lambda: [0, 0])
    for m, ref, src, med, s, c in json.load(open(os.path.join(HERE, 'sessions_raw.json'))):
        ch = session_channel(ref, src, med)
        if not ch: continue
        x = ses[(m[:7], ch)]; x[0] += int(s); x[1] += int(c)
    blog = defaultdict(lambda: [0, 0])
    for m, ref, s, c in json.load(open(os.path.join(HERE, 'blog_sessions.json'))):
        ch = {'search': 'org_search', 'social': 'org_social', 'email': 'email'}.get(ref, 'direct')
        x = ses[(m[:7], ch)]; x[0] -= int(s); x[1] -= int(c)
        b = blog[m[:7]]; b[0] += int(s); b[1] += int(c)
    months = sorted({m for (_, m, _) in agg} | {m for (m, _) in ses})
    out = dict(generated=dt.date.today().isoformat(), channels=CH, months=months, aff_commission=AFF_COMMISSION,
               spend={m: dict(meta=store.get(m, {}).get('meta'), google=store.get(m, {}).get('google')) for m in months},
               sessions={m: {ch: ses.get((m, ch), [0, 0]) for ch in CH} for m in months},
               blog={m: blog.get(m, [0, 0]) for m in months},
               data={model: {m: {ch: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in agg[(model, m, ch)].items()}
                                     for ch in CH + ['none'] if (model, m, ch) in agg} for m in months} for model in ('first', 'last')},
               orders_total=len(orders))
    p = os.path.join(HERE, '..', 'site', 'public', 'channels.json')
    json.dump(out, open(p, 'w'), separators=(',', ':'))
    # quick report
    for model in ('last', 'first'):
        print(model)
        for ch in CH + ['none']:
            rs = [out['data'][model][m][ch] for m in months if ch in out['data'][model][m] and m >= '2026-01']
            if not rs: continue
            o_ = sum(r['orders'] for r in rs); rv = sum(r['rev'] for r in rs); nw = sum(r['new'] for r in rs)
            print(f"  {ch:11} orders {o_:5}  rev {rv:>10,.0f}  aov {rv/max(o_,1):5.0f}  new {nw:5}")


if __name__ == '__main__':
    main()
