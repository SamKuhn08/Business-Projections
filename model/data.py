"""Loads store.json into the series the model uses.
MONTHS = every month with Shopify data (the last one may be partial).
CUT    = number of complete months (the model fits on MONTHS[:CUT]).
"""
import json, os
_S = json.load(open(os.path.join(os.path.dirname(__file__), 'store.json')))
META_INFO = _S['meta']
_M = _S['months']
MONTHS = sorted(m for m, r in _M.items() if r.get('orders') is not None)
PARTIAL = META_INFO.get('partial_month')
CUT = MONTHS.index(PARTIAL) if PARTIAL in MONTHS else len(MONTHS)
MTD_DAYS = META_INFO.get('mtd_days')
COGS_PCT = META_INFO.get('cogs_pct', 0.24)
ROGAN = set(META_INFO['rogan'])
BLOG_DEINDEX = META_INFO['blog_deindex']

col = lambda k: [_M[m].get(k) for m in MONTHS]
ORDERS, NET, SESSIONS, CHECKOUTS = col('orders'), col('net'), col('sessions'), col('checkouts')
LP = {k: [tuple(_M[m]['lp'][k]) for m in MONTHS] for k in 'PCHLB'}
REF = {k: [tuple(_M[m]['ref'][k]) for m in MONTHS] for k in ('search', 'direct', 'social')}
LINE_IN, LINE_HA = col('line_in'), col('line_ha')
EMAIL_REV, SIGNUPS = col('email_rev'), col('signups')
AMAZON, META, GOOGLE, AMZ_ADS = col('amazon'), col('meta'), col('google'), col('amz_ads')
SHEET_SHOP = col('sheet_shop')


def month_field(m, k):
    return _M.get(m, {}).get(k)
SMS_REV, SMS_SIGNUPS, POPUP_IMPR = col('sms_rev'), col('sms_signups'), col('popup_impr')
SMS_IMPORTS = set(META_INFO.get('sms_imports', []))
# O&F Targets tab (official monthly P&L, completed months)
TGT = {k: col('tgt_' + k) for k in ('shop', 'amz', 'ddo', 'total', 'cogs', 'ad', 'other_sma', 'payroll', 'ga', 'other', 'ni')}
DDO = TGT['ddo']
# Branded search: Google 'iron neck' query clicks/impressions (Search Console), Amazon Brand Analytics
BRAND_CLICKS, BRAND_IMPR, GSC_DAYS = col('gsc_brand_clicks'), col('gsc_brand_impr'), col('gsc_days')
AMZ_BRAND = col('amz_brand_searches')
MTD = META_INFO.get('mtd') or {}
