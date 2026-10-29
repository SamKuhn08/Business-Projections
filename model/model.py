"""Iron Neck revenue projection model (driver based).

D2C (Shopify) revenue = orders x AOV
  orders  = k x sum_seg( sessions_seg x CR_seg )      k = orders per completed checkout
  segments: P product, C collection, H homepage, L landing pages, B blog, O other
Each driver = damped trend x seasonal index. Seasonal indices come from ratio-to-centered
12 month moving average, with known one-offs masked (Rogan Q1 2025 on brand-demand series,
blog deindex Mar 2025 - Apr 2026 on blog, Klaviyo bulk imports on signups).
Amazon sales and Amazon ads: own seasonal index from full Sellerboard history x damped trend.
Meta, Google, SMS: D2C order seasonality ** beta x damped trend (sparse history).
"""
import json, math, datetime, datetime as dt_, statistics as st
from data import *
from data import _M
import events as EV
_ORDERS, _NET = ORDERS, NET
_all_months = lambda: _M.keys()

PHI = 0.90          # monthly trend damping
LAMBDA = 1.0        # seasonal shrinkage exponent (1 = full index, 0 = flat); calibrated by rolling backtest
O_LEVEL_N = 3
ORD_REC_POW = 1     # recency weight power for the order seasonal index (tuned by rolling backtest)
MAX_SLOPE = 0.04    # cap on monthly log trend
SEGS = ['P', 'C', 'H', 'L', 'B', 'O']
BRAND_SEGS = {'H'}
HORIZON_END = '2027-12'  # projection always runs at least to here, and at least 12 months out


def mon(ym): return int(ym[5:7])


def interp(xs, mask):
    """Replace masked points by log-linear interpolation of neighbours (for seasonal fitting only)."""
    out = list(xs)
    idx = [i for i in range(len(xs)) if not mask[i] and xs[i] and xs[i] > 0]
    for i in range(len(xs)):
        if mask[i] or not xs[i] or xs[i] <= 0:
            lo = max([j for j in idx if j < i], default=None)
            hi = min([j for j in idx if j > i], default=None)
            if lo is None: out[i] = xs[hi]
            elif hi is None: out[i] = xs[lo]
            else:
                w = (i - lo) / (hi - lo)
                out[i] = math.exp(math.log(xs[lo]) * (1 - w) + math.log(xs[hi]) * w)
    return out


HOLIDAY_REC_POW = 6      # Nov/Dec weight the latest years hardest: the BFCM peak shrinks every year (Nov 4.1x Sep-Oct in 2023, 2.6x 2024, 2.1x 2025)


def seasonal_index(xs, months, mask, rec_pow=1, holiday_pow=None):
    """Ratio to centred 12-month moving average; median per calendar month; normalised to mean 1."""
    x = interp(xs, mask)
    n = len(x)
    ratios = {m: [] for m in range(1, 13)}
    for t in range(6, n - 6):
        cma = (0.5 * x[t - 6] + sum(x[t - 5:t + 6]) + 0.5 * x[t + 6]) / 12
        if not mask[t]:
            p = holiday_pow if (holiday_pow is not None and mon(months[t]) in (11, 12)) else rec_pow
            w = (int(months[t][:4]) - 2022) ** p   # recency weight: 2023=1 ... 2026=4 (rec_pow 1)
            ratios[mon(months[t])].append((min(3.5, max(0.3, xs[t] / cma)), w))
    s = {m: (math.exp(sum(w * math.log(v) for v, w in r) / sum(w for _, w in r)) if r else 1.0)
         for m, r in ratios.items()}
    s = {m: v ** LAMBDA for m, v in s.items()}
    mean = sum(s.values()) / 12
    return {m: v / mean for m, v in s.items()}


def theil_sen(ys):
    pts = [(i, y) for i, y in enumerate(ys) if y is not None]
    sl = [(b[1] - a[1]) / (b[0] - a[0]) for k, a in enumerate(pts) for b in pts[k + 1:]]
    return st.median(sl) if sl else 0.0


def project(xs, months, mask, horizon_months, level_n=3, trend_n=12, seas=None, trend=True):
    """Return (forecast list, seasonal index, monthly slope)."""
    s = seas or seasonal_index(xs, months, mask)
    d = [math.log(xs[i] / s[mon(months[i])]) if (xs[i] and xs[i] > 0 and not mask[i]) else None
         for i in range(len(xs))]
    recent = [v for v in d[-trend_n:]]
    slope = theil_sen(recent) if trend else 0.0
    slope = max(-MAX_SLOPE, min(MAX_SLOPE, slope))
    lv = [v for v in d[-level_n:] if v is not None]
    if not lv:  # recent months all masked (e.g. blog during deindex): level = what is actually happening
        lv = [math.log(xs[i] / s[mon(months[i])]) for i in range(len(xs) - level_n, len(xs)) if xs[i] > 0]
    level = sum(lv) / len(lv)
    # level is centred ~ (level_n-1)/2 months before the last point; move it to the last point
    level += slope * (level_n - 1) / 2
    out, cum, damp = [], 0.0, 1.0
    for h, ym in enumerate(horizon_months, 1):
        damp *= PHI
        cum += damp
        out.append(math.exp(level + slope * cum) * s[mon(ym)])
    return out, s, slope


def shop_bias(*a, **k):
    """Bias correction always uses the in-sample horizon (backtest of past 1-3 month forecasts)."""
    global FULL_HORIZON
    save, FULL_HORIZON = FULL_HORIZON, False
    try: return _shop_bias(*a, **k)
    finally: FULL_HORIZON = save


FULL_HORIZON = False   # projection_log.py: reconstructed snapshots project all the way to HORIZON_END


def fit(cut, events=True):
    """Fit on months[:cut] and forecast the next len(horizon) months. Returns dict of series.
    events=True: movable promos (promos.json) are measured from daily orders and taken out of the order and
    revenue history before fitting, then added back on their dates in the horizon (see events.py)."""
    months = MONTHS[:cut]
    upto = MONTHS[cut] + '-01' if cut < len(MONTHS) else None
    adj = EV.history_adjustment(upto) if events else {}
    ORDERS = [_ORDERS[i] - (adj.get(MONTHS[i], [0, 0])[0] if i < cut else 0) for i in range(len(MONTHS))]
    NET = [_NET[i] - (adj.get(MONTHS[i], [0, 0])[1] if i < cut else 0) for i in range(len(MONTHS))]
    last = months[-1]
    y, m = int(last[:4]), int(last[5:7])
    horizon = []
    n_ahead = (CUT - cut) if (cut < CUT and not FULL_HORIZON) else max(12, (int(HORIZON_END[:4]) - y) * 12 + int(HORIZON_END[5:]) - m)
    for _ in range(n_ahead):
        m += 1
        if m == 13: y, m = y + 1, 1
        horizon.append(f"{y}-{m:02d}")
    rog = [ym in ROGAN for ym in months]
    blog_mask = [ym in BLOG_DEINDEX for ym in months]
    none = [False] * cut

    # ---- segment sessions and conversions
    ses, conv = {}, {}
    for sgm in 'PCHLB':
        ses[sgm] = [LP[sgm][i][0] for i in range(cut)]
        conv[sgm] = [LP[sgm][i][1] for i in range(cut)]
    ses['O'] = [SESSIONS[i] - sum(LP[s][i][0] for s in 'PCHLB') for i in range(cut)]
    conv['O'] = [CHECKOUTS[i] - sum(LP[s][i][1] for s in 'PCHLB') for i in range(cut)]

    # Seasonality is applied ONCE, at the order level. Segment drivers set level and trend:
    #   orders(h) = orders_level x traffic_growth(h) x S_orders(month)
    #   traffic_growth = sum_seg(base_ses_seg(h) x CR_seg) / sum_seg(base_ses_seg(0) x CR_seg)
    nb_ses = [SESSIONS[i] - LP['B'][i][0] for i in range(cut)]
    s_ses = seasonal_index(nb_ses, months, rog)
    s_ord = seasonal_index(ORDERS[:cut], months, rog, rec_pow=ORD_REC_POW, holiday_pow=HOLIDAY_REC_POW)
    # Quality-weighted traffic index Q = sum_seg sessions x CR_seg (CR over last 12 months).
    # Trend is read off Q, not off individual segments, so traffic shifting between page types
    # (e.g. homepage -> product pages after a nav change) is not mistaken for growth, and a
    # blog surge only moves Q by what blog sessions actually convert.
    seg_cr = {}
    for sgm in SEGS:
        num = sum(conv[sgm][i] for i in range(cut - 12, cut))
        den = sum(ses[sgm][i] for i in range(cut - 12, cut))
        seg_cr[sgm] = num / den if den else 0.0
    Q = [sum(ses[sg][i] * seg_cr[sg] for sg in SEGS if sg != 'B') for i in range(cut)]
    _, _, q_slope = project(Q, months, rog, horizon, level_n=3, seas=s_ses)
    growth, cum, damp = [], 0.0, 1.0
    for h in range(len(horizon)):
        damp *= PHI; cum += damp
        growth.append(math.exp(q_slope * cum))
    seg_base, seg_f = {}, {}
    for sgm in SEGS:
        seas = s_ses if sgm != 'B' else {m: 1.0 for m in range(1, 13)}
        lvl = st.mean(ses[sgm][i] / seas[mon(months[i])] for i in range(cut - O_LEVEL_N, cut))
        g = growth if sgm != 'B' else [1.0] * len(horizon)
        seg_base[sgm] = [lvl * g[h] for h in range(len(horizon))]
        seg_f[sgm] = [seg_base[sgm][h] * seas[mon(horizon[h])] for h in range(len(horizon))]
    # blog contributes its own (tiny) orders on top of the non-blog index
    blog_orders = [seg_f['B'][h] * seg_cr['B'] for h in range(len(horizon))]
    lvl_ix = [i for i in range(cut - 1, -1, -1) if months[i] not in ROGAN][:O_LEVEL_N]     # Rogan months never set the level
    o_level = st.mean((ORDERS[i] - LP['B'][i][1]) / s_ord[mon(months[i])] for i in lvl_ix)
    k = 1.0
    s_cr = s_ord
    # AOV from product mix: 1/AOV = (1-h)/a_IN + h/a_HA, h = Harness+accessory revenue share.
    # Fitted on the trailing 18 months (least squares, linear in 1/a). Captures the 2026 funnel
    # split: more Harness orders -> higher conversion, lower AOV.
    ha_hist = [LINE_HA[i] / (LINE_IN[i] + LINE_HA[i]) for i in range(cut)]
    lo = max(0, cut - 18)
    sxx = sum((1 - ha_hist[i]) ** 2 for i in range(lo, cut)); syy = sum(ha_hist[i] ** 2 for i in range(lo, cut))
    sxy = sum((1 - ha_hist[i]) * ha_hist[i] for i in range(lo, cut))
    bx = sum((1 - ha_hist[i]) * ORDERS[i] / NET[i] for i in range(lo, cut)); by = sum(ha_hist[i] * ORDERS[i] / NET[i] for i in range(lo, cut))
    det = sxx * syy - sxy ** 2
    inv_in = (bx * syy - by * sxy) / det; inv_ha = (by * sxx - bx * sxy) / det
    a_in, a_ha = 1 / inv_in, 1 / inv_ha
    ha_f, _, _ = project(ha_hist, months, none, horizon, level_n=6)
    ha_f = [min(0.75, max(0.02, v)) for v in ha_f]
    # residual multiplier keeps the recent level honest (last 6 months actual / mix-implied)
    if 0 < a_ha < a_in:
        resid = st.mean((NET[i] / ORDERS[i]) * ((1 - ha_hist[i]) / a_in + ha_hist[i] / a_ha) for i in range(cut - 3, cut))
        aov_f = [resid / ((1 - h) / a_in + h / a_ha) for h in ha_f]
    else:  # not enough mix variation to identify the two price points yet: plain trend x season
        resid = 1.0
        aov_f, _, _ = project([NET[i] / ORDERS[i] for i in range(cut)], months, rog, horizon, level_n=3)
    aov_s = None
    orders_f = [o_level * growth[h] * s_ord[mon(horizon[h])] + blog_orders[h] for h in range(len(horizon))]
    d2c_f = [orders_f[h] * aov_f[h] for h in range(len(horizon))]
    # ---- promos back on their dates: lift (in baseline days) x the month's baseline orders / revenue per day
    import calendar
    promo_o, promo_r, promo_ev = [0.0] * len(horizon), [0.0] * len(horizon), [[] for _ in horizon]
    if events:
        lifts = EV.type_lifts(upto)
        for typ, share, e in EV.future_events(horizon[0] + '-01'):
            lo_, lr_ = EV.event_lift(e, lifts)
            for mon_, sh in share.items():
                if mon_ not in horizon: continue
                h = horizon.index(mon_)
                nd = calendar.monthrange(int(mon_[:4]), int(mon_[5:]))[1]
                promo_o[h] += lo_ * sh * orders_f[h] / nd
                promo_r[h] += lr_ * sh * d2c_f[h] / nd
                promo_ev[h].append(typ)
    d2c_base = list(d2c_f); orders_base = list(orders_f)
    orders_f = [orders_f[h] + promo_o[h] for h in range(len(horizon))]
    d2c_f = [d2c_f[h] + promo_r[h] for h in range(len(horizon))]

    # ---- product line split (share of Harness/accessories within IN+HA), coverage of NET
    cov = sum((LINE_IN[i] + LINE_HA[i]) / _NET[i] for i in range(cut - 6, cut)) / 6

    # ---- email / signups
    sig_clean = list(SIGNUPS[:cut])
    sig_mask = []
    for i in range(cut):
        ref = st.median(SIGNUPS[max(0, i - 6):i + 7])
        sig_mask.append(SIGNUPS[i] > 3 * ref)
    sig_f, _, _ = project(sig_clean, months, sig_mask, horizon, level_n=6)
    em_f, _, _ = project(EMAIL_REV[:cut], months, rog, horizon, level_n=6)
    sms_ok = [SMS_REV[i] is not None and SMS_REV[i] > 0 for i in range(cut)]
    sms_f, _, _ = project([SMS_REV[i] or 1 for i in range(cut)], months, [not o for o in sms_ok], horizon, level_n=6)
    ss = [SMS_SIGNUPS[i] if (SMS_SIGNUPS[i] and months[i] not in SMS_IMPORTS) else None for i in range(cut)]
    ss_mask = [v is None for v in ss]
    if sum(1 for v in ss if v) >= 12:
        ss_f, _, _ = project([v or 1 for v in ss], months, ss_mask, horizon, level_n=6)
    else:
        ss_f = [None] * len(horizon)

    return dict(d2c_base=d2c_base, orders_base=orders_base, promo_o=promo_o, promo_r=promo_r, promo_ev=promo_ev, adj=adj, a_in=a_in, a_ha=a_ha, aov_resid=resid, q_slope=q_slope, Q=Q, horizon=horizon, seg_base=seg_base, growth=growth, o_level=o_level, s_ord=s_ord, s_ses=s_ses, seg_f=seg_f, seg_cr=seg_cr, k=k, aov_f=aov_f, orders_f=orders_f,
                d2c_f=d2c_f, ha_f=ha_f, cov=cov, sig_f=sig_f, em_f=em_f, sms_f=sms_f, ss_f=ss_f, s_cr=s_cr, aov_s=aov_s,
                sig_mask=sig_mask)


# ---------------------------------------------------------------- sparse series (Amazon, ad spend)
def project_sparse(series, horizon, s_base, cut=None):
    cut = cut or CUT
    """Seasonality = D2C order seasonality ** beta, beta fitted by regressing log(series) on
    log(s_base) with a separate intercept per year (so level differences between years do not
    leak into the seasonal fit). Trend = damped Theil-Sen on the de-seasonalised 2026 months."""
    pts = [(i, series[i]) for i in range(cut) if series[i]]
    years = sorted({MONTHS[i][:4] for i, _ in pts})
    # demean within year, then slope through origin
    num = den = 0.0
    for y in years:
        grp = [(math.log(s_base[mon(MONTHS[i])]), math.log(v)) for i, v in pts if MONTHS[i][:4] == y]
        if len(grp) < 3: continue
        mx = st.mean(g[0] for g in grp); my = st.mean(g[1] for g in grp)
        num += sum((gx - mx) * (gy - my) for gx, gy in grp); den += sum((gx - mx) ** 2 for gx, _ in grp)
    beta = num / den if den else 1.0
    seas = {m: s_base[m] ** beta for m in range(1, 13)}
    mean = sum(seas.values()) / 12
    seas = {m: v / mean for m, v in seas.items()}
    recent = [(i, v) for i, v in pts if i >= cut - 12]
    d = [math.log(v / seas[mon(MONTHS[i])]) for i, v in recent]
    slope = max(-MAX_SLOPE, min(MAX_SLOPE, theil_sen(d)))
    level = sum(d[-3:]) / 3 + slope
    out, cum, damp = [], 0.0, 1.0
    for ym in horizon:
        damp *= PHI; cum += damp
        out.append(math.exp(level + slope * cum) * seas[mon(ym)])
    return out, seas, slope, beta


AMZ_HOLIDAY_POW = 6    # Amazon's Nov/Dec peak shrinks every year too: weight the latest years hardest
AMZ_REC_POW = 3     # Amazon's Q4 peak has shrunk every year (Nov/Oct 2.6x 2023, 2.0x 2024, 1.25x 2025): weight recent years harder
AMZ_LEVEL_N = 6     # with Prime taken out as dated events, 21-origin backtest: 6 months 25.8% vs 3 months 29.5% MAPE (2026-10-19)


def project_amazon(series, cut, horizon, start=0, level_n=None):
    """Amazon sales / Amazon ads / brand search. Own seasonal index (recency^3, Rogan masked),
    level = mean of the last 3 de-seasonalised months, slope = trailing-12-month YoY growth (Rogan pairs
    dropped), damped. Rolling backtest on Amazon: MAPE 28.9% vs 33% for Theil-Sen on 12 months.
    `start` lets a series with a shorter history (brand search from 2024) skip the empty months."""
    level_n = level_n or AMZ_LEVEL_N
    ms = MONTHS[start:cut]
    ser = series[start:cut]
    mask = [m in ROGAN or not ser[i] for i, m in enumerate(ms)]
    s = seasonal_index(ser, ms, mask, rec_pow=AMZ_REC_POW, holiday_pow=AMZ_HOLIDAY_POW)
    n = len(ms)
    pairs = [(ser[i], ser[i - 12]) for i in range(max(12, n - 12), n)
             if ser[i] and ser[i - 12] and ms[i] not in ROGAN and ms[i - 12] not in ROGAN]
    slope = math.log(sum(a for a, _ in pairs) / sum(b for _, b in pairs)) / 12 if pairs else 0.0
    slope = max(-MAX_SLOPE, min(MAX_SLOPE, slope))
    d = [math.log(ser[i] / s[mon(ms[i])]) for i in range(n - level_n, n) if not mask[i]]
    level = sum(d) / len(d) + slope * (level_n + 1) / 2
    out, cum, damp = [], 0.0, 1.0
    for ym in horizon:
        damp *= PHI; cum += damp
        out.append(math.exp(level + slope * cum) * s[mon(ym)])
    return out, s, slope


def project_amazon_events(cut, horizon, events=True):
    """Amazon with Prime as dated events: Prime lift (Seller Board, scaled to the books basis) is taken out of
    history, the series is projected with project_amazon, and Prime is added back in the month it falls in
    (promos.json 'amazon' dates) at the recency-weighted lift for its type x that month's baseline per day."""
    import calendar
    if not events:
        f, s, sl = project_amazon(AMAZON, cut, horizon)
        return f, s, sl, [0.0] * len(horizon)
    upto = MONTHS[cut] + '-01' if cut < len(MONTHS) else None
    adj = EV.amazon_history_adjustment(upto)
    ser = list(AMAZON)
    for m, x in adj.items():
        if m in MONTHS:
            i = MONTHS.index(m)
            if i < cut and ser[i]:
                sb = month_field(m, 'sb_sales') or ser[i]
                ser[i] = ser[i] - x * ser[i] / sb          # Seller Board gross -> books basis
    # ensemble of a short (3 month) and a long (12 month) level: the short one reacts to turns (spring 2026 ran
    # +40% YoY, September +1%), the long one is steadier; 21-origin backtest 24.9% MAPE vs 29.0% (3) and 25.2% (6)
    f3, s, sl = project_amazon(ser, cut, horizon, level_n=3)
    f12, _, _ = project_amazon(ser, cut, horizon, level_n=12)
    f = [math.sqrt(x * y) for x, y in zip(f3, f12)]
    lifts = EV.amazon_lifts(upto)
    add = [0.0] * len(horizon)
    for typ, mon_ in EV.amazon_future(horizon[0] + '-01'):
        if mon_ in horizon and typ in lifts:
            h = horizon.index(mon_)
            nd = calendar.monthrange(int(mon_[:4]), int(mon_[5:]))[1]
            add[h] += lifts[typ] * f[h] / nd
    return [f[h] + add[h] for h in range(len(horizon))], s, sl, add


# ---------------------------------------------------------------- elasticities
def elasticity(xs, ys):
    lx = [math.log(v) for v in xs]; ly = [math.log(v) for v in ys]
    mx, my = st.mean(lx), st.mean(ly)
    cov = sum((a - mx) * (b - my) for a, b in zip(lx, ly))
    var = sum((a - mx) ** 2 for a in lx)
    return cov / var


BIAS_N = 54          # Shopify bias correction: mean log error of the last 54 realised 1-3 month forecasts
_FITS = {}


def _fit_cached(cut):
    if cut not in _FITS: _FITS[cut] = fit(cut)
    return _FITS[cut]


def _shop_bias(cut):
    """Multiplier that removes the Shopify model's recent average error, using only forecasts whose months had
    closed before `cut` (21-origin backtest: bias +9% -> -1%, MAPE 24.0% -> 23.1%)."""
    errs = []
    for o in range(max(13, cut - 26), cut):
        g = _fit_cached(o)
        for h, ym in enumerate(g['horizon'][:3]):
            i = MONTHS.index(ym)
            if i < cut and ym not in ROGAN: errs.append((ym, math.log(g['d2c_f'][h] / NET[i])))
    errs = [e for _, e in sorted(errs)][-BIAS_N:]
    return math.exp(-st.mean(errs)) if errs else 1.0


def apply_bias(g, corr):
    for k in ('d2c_f', 'orders_f', 'd2c_base', 'orders_base', 'promo_o', 'promo_r'):
        g[k] = [v * corr for v in g[k]]
    g['bias_corr'] = corr
    return g


def main():
    # ---------- rolling-origin backtest: refit at 5 origins, forecast up to 12 months ahead
    origins = []
    by_h = {h: [] for h in range(12)}
    snaive_err = []
    for cut in (CUT - 15, CUT - 12, CUT - 9, CUT - 6, CUT - 3):
        g = apply_bias(dict(fit(cut)), shop_bias(cut))
        rows = []
        for h, ym in enumerate(g['horizon'][:12]):
            i = MONTHS.index(ym)
            e = g['d2c_f'][h] / NET[i] - 1
            rows.append(dict(month=ym, actual=NET[i], forecast=round(g['d2c_f'][h]), err=e))
            by_h[h].append(abs(e)); snaive_err.append(abs(NET[i - 12] / NET[i] - 1))
        origins.append(dict(origin=MONTHS[cut - 1], rows=rows,
                            mape=st.mean(abs(r['err']) for r in rows), bias=st.mean(r['err'] for r in rows)))
    mape = st.mean(e for v in by_h.values() for e in v)
    bt_total_err = st.mean(o['bias'] for o in origins)
    bt_rows = origins[1]['rows']
    h_ape = [st.median(by_h[h]) for h in range(12)]
    # ---------- production fit through Sep 2026 (Oct is partial)
    f = apply_bias(dict(fit(CUT)), shop_bias(CUT))
    H = f['horizon']
    so = f['s_ord']
    # Amazon has full history (Sellerboard, Jan 2023 on): its own seasonal index, Rogan months masked
    amz_f, amz_s, amz_slope, amz_promo = project_amazon_events(CUT, H)
    amz_beta = 1.0
    meta_f, meta_s, meta_slope, meta_beta = project_sparse(META, H, so)
    gg_f, _, gg_slope, gg_beta = project_sparse(GOOGLE, H, so)
    aa_f, _, aa_slope = project_amazon(AMZ_ADS, CUT, H)
    aa_beta = 1.0
    # ---------- brand search (Google 'iron neck' clicks, Jan 2024 on); Rogan masked, never projected to recur
    b0 = next(i for i, v in enumerate(BRAND_CLICKS) if v)
    brand_f, brand_s, brand_slope = project_amazon(BRAND_CLICKS, CUT, H, start=b0)
    # Rogan, measured: Jan-Feb 2025 vs Jan-Feb 2024 scaled by the Mar-Dec 2025/2024 change (the rest of the year)
    def excess(series):
        ix = {m: i for i, m in enumerate(MONTHS)}
        r = sum(series[ix[f'2025-{k:02d}']] for k in range(3, 13)) / sum(series[ix[f'2024-{k:02d}']] for k in range(3, 13))
        act = [series[ix[f'2025-0{k}']] for k in (1, 2)]
        cf = [series[ix[f'2024-0{k}']] * r for k in (1, 2)]
        return dict(actual=sum(act), baseline=sum(cf), excess=sum(act) - sum(cf))
    rogan = dict(clicks=excess(BRAND_CLICKS), shop=excess(TGT['shop']), amz=excess(TGT['amz']), orders=excess(ORDERS))
    rogan['per_click'] = (rogan['shop']['excess'] + rogan['amz']['excess']) / rogan['clicks']['excess']
    # brand elasticity: YoY log change in orders / Amazon vs YoY log change in brand clicks, Rogan months left out
    yy = [(math.log(BRAND_CLICKS[i] / BRAND_CLICKS[i - 12]), math.log(ORDERS[i] / ORDERS[i - 12]), math.log(TGT['amz'][i] / TGT['amz'][i - 12]))
          for i in range(12, CUT) if BRAND_CLICKS[i] and BRAND_CLICKS[i - 12] and TGT['amz'][i] and MONTHS[i] not in ROGAN]
    def _sl(xs, ys):
        mx, my = st.mean(xs), st.mean(ys)
        return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sum((a - mx) ** 2 for a in xs)
    e_brand = max(0.1, min(0.6, _sl([a for a, _, _ in yy], [b for _, b, _ in yy])))
    e_brand_amz = max(0.1, min(0.6, _sl([a for a, _, _ in yy], [c for _, _, c in yy])))

    # ---------- D/D/O (direct, distributor, other). Sam: pre-2026 DDO included Amazon resellers that are gone,
    # so only Jan 2026 on is used: flat at the 2026 average, no trend, no seasonality.
    ddo26 = [DDO[i] for i in range(CUT) if MONTHS[i] >= '2026-01' and DDO[i] is not None]
    ddo_level = st.mean(ddo26)
    ddo_f = [ddo_level] * len(H)

    # ---------- P&L on the O&F Targets basis (last 12 closed months)
    l12 = range(CUT - 12, CUT)
    cogs_pct = sum(TGT['cogs'][i] for i in l12) / sum(TGT['total'][i] for i in l12)
    sheet_ads = lambda i: (META[i] or 0) + (GOOGLE[i] or 0) + (AMZ_ADS[i] or 0)
    ad_ratio = sum(TGT['ad'][i] for i in l12) / sum(sheet_ads(i) for i in l12)
    # SMS: Attentive era only (Nov 2024 on), demand-calendar seasonality like Amazon and spend
    sms_att = [SMS_REV[i] if MONTHS[i] >= '2024-12' else None for i in range(len(MONTHS))]
    f['sms_f'], _, sms_slope, sms_beta = project_sparse(sms_att, H, so)
    amz_ratio = amz_beta

    # finance basis: sheet Shopify revenue vs Shopify analytics net sales (Jan-Sep 2026)
    fm = [i for i in range(CUT - 12, CUT) if TGT['shop'][i]]
    fin_ratio = sum(TGT['shop'][i] for i in fm) / sum(NET[i] for i in fm)

    # elasticities (2026, clean months)
    em = [i for i in range(CUT - 9, CUT) if META[i]]
    e_meta = elasticity([META[i] for i in em], [LP['L'][i][0] + REF['social'][i][0] for i in em])
    e_meta = max(0.2, min(1.0, e_meta))
    clean = [i for i in range(6, CUT) if not f['sig_mask'][i] and MONTHS[i] not in ROGAN]
    roll = lambda i: st.mean(SIGNUPS[j] if not f['sig_mask'][j] else st.median(SIGNUPS[max(0, j-6):j+7]) for j in range(i - 5, i + 1))
    e_email = elasticity([roll(i) for i in clean], [EMAIL_REV[i] for i in clean])
    sms_i = [i for i in range(CUT) if SMS_SIGNUPS[i] and MONTHS[i] >= '2025-04' and SMS_REV[i]]
    rs = lambda i: st.mean(SMS_SIGNUPS[j] for j in range(i - 5, i + 1) if SMS_SIGNUPS[j] and MONTHS[j] not in SMS_IMPORTS)
    e_sms = max(0.2, min(0.8, elasticity([rs(i) for i in sms_i], [SMS_REV[i] for i in sms_i]))) if len(sms_i) >= 8 else 0.5

    # uncertainty band from backtest: per-horizon APE, smoothed
    band, run = [], 0.0
    for h in range(len(H)):
        run = max(run, h_ape[min(h, 11)])          # monotone: uncertainty never shrinks with horizon
        band.append(min(0.45, max(0.08, run)))

    # fixed costs (other SMA, payroll, G&A, other): our own projection from history, the mean of the last 3 closed
    # months on the Targets basis. The finance sheet's forward rows are O&F's plan, so they are not used.
    tgt_fixed = [TGT['total'][i] - TGT['cogs'][i] - TGT['ad'][i] - TGT['ni'][i] for i in range(CUT)
                 if None not in (TGT['total'][i], TGT['cogs'][i], TGT['ad'][i], TGT['ni'][i])]
    fixed_level = st.mean(tgt_fixed[-3:])
    fixed = [fixed_level] * len(H)
    fut = []
    for h, ym in enumerate(H):
        row = dict(month=ym,
                   seg={s: dict(ses=round(f['seg_f'][s][h]), base=f['seg_base'][s][h], cr=f['seg_cr'][s]) for s in SEGS}, growth=f['growth'][h], s_ord=f['s_ord'][mon(ym)], o_level=f['o_level'],
                   k=f['k'], aov=f['aov_f'][h], orders=f['orders_f'][h], d2c=f['d2c_f'][h],
                   ha_share=f['ha_f'][h], cov=f['cov'], signups=f['sig_f'][h], email=f['em_f'][h], sms=f['sms_f'][h], sms_signups=f['ss_f'][h],
                   d2c_base=f['d2c_base'][h], promo_d2c=f['promo_r'][h], promo_orders=f['promo_o'][h], promos=f['promo_ev'][h], amz_promo=amz_promo[h],
                   amazon=amz_f[h], meta=meta_f[h], google=gg_f[h], amz_ads=aa_f[h], ddo=ddo_f[h],
                   brand_clicks=brand_f[h], fixed=fixed[h], band=band[h])
        fut.append(row)
    # ---------- nowcast the month in progress: actual month-to-date (daily net income sheet, closed days)
    # plus the model's full-month value x share of the month still to come
    nowcast = None
    if H and H[0] == PARTIAL and MTD.get('month') == PARTIAL:
        import calendar
        y, m = int(PARTIAL[:4]), int(PARTIAL[5:])
        left = 1 - MTD['days'] / calendar.monthrange(y, m)[1]
        r0 = fut[0]
        model_full = {k: r0[k] for k in ('d2c', 'amazon', 'meta', 'google', 'amz_ads')}
        # promos already run this month are in the actuals: only the baseline is scaled by the share left
        # (plus any promo still to come after the closed days)
        later = EV.future_events((dt_.date.fromisoformat(MTD['through']) + dt_.timedelta(1)).isoformat())
        promo_left = sum(sh for t, shr, e in later for mm, sh in shr.items() if mm == PARTIAL)
        d2c_now = MTD['shop'] / fin_ratio + r0['d2c_base'] * left + (r0['promo_d2c'] * promo_left if r0['promo_d2c'] else 0)
        kk = d2c_now / r0['d2c']
        r0['orders'] *= kk; r0['d2c'] = d2c_now
        for k in ('amazon', 'meta', 'google', 'amz_ads'):
            base_k = r0[k] - (r0['amz_promo'] if k == 'amazon' else 0)
            if MTD.get(k) is not None: r0[k] = MTD[k] + base_k * left
        r0['nowcast'] = True
        nowcast = dict(through=MTD['through'], days=MTD['days'], share_left=left, mtd=MTD, model_full=model_full)

    hist = []
    for i, ym in enumerate(MONTHS):
        hist.append(dict(month=ym, d2c=NET[i], orders=ORDERS[i], sessions=SESSIONS[i],
                         aov=NET[i] / ORDERS[i], cr=CHECKOUTS[i] / SESSIONS[i],
                         seg={s: dict(ses=LP[s][i][0], conv=LP[s][i][1]) for s in 'PCHLB'},
                         brand=LP['H'][i][0] + REF['direct'][i][0],
                         line_in=LINE_IN[i], line_ha=LINE_HA[i], email=EMAIL_REV[i], signups=SIGNUPS[i],
                         sms=SMS_REV[i], sms_signups=(None if MONTHS[i] in SMS_IMPORTS else SMS_SIGNUPS[i]), popup_impr=POPUP_IMPR[i],
                         signup_import=f['sig_mask'][i] if i < CUT else False,
                         amazon=AMAZON[i], meta=META[i], google=GOOGLE[i], amz_ads=AMZ_ADS[i],
                         ddo=DDO[i], tgt={k: TGT[k][i] for k in TGT}, brand_clicks=BRAND_CLICKS[i], brand_impr=BRAND_IMPR[i],
                         brand_days=GSC_DAYS[i], amz_brand=AMZ_BRAND[i],
                         partial=(i >= CUT)))

    last6 = range(CUT - 6, CUT)
    ck6 = sum(CHECKOUTS[i] for i in last6)
    shares = dict(social=sum(REF['social'][i][1] for i in last6) / ck6,
                  search=sum(REF['search'][i][1] for i in last6) / ck6,
                  direct=sum(REF['direct'][i][1] for i in last6) / ck6)
    out = dict(shares=shares, a_in=f['a_in'], a_ha=f['a_ha'],
        generated=datetime.date.today().isoformat(), partial_month=PARTIAL, mtd_days=MTD_DAYS, fin_ratio=fin_ratio, cogs=cogs_pct,
        hist=hist, fut=fut, s_ord=f['s_ord'],
        goals={m: dict(shop=month_field(m, 'goal_shop'), amz=month_field(m, 'goal_amz'), ddo=month_field(m, 'goal_ddo'), ni=month_field(m, 'goal_ni'))
               for m in sorted(_all_months()) if month_field(m, 'goal_shop')},
        pnl=dict(cogs_pct=cogs_pct, ad_ratio=ad_ratio, ddo_level=ddo_level, ddo_2026=ddo26),
        brand=dict(e_shop=e_brand, e_amz=e_brand_amz, slope=brand_slope, rogan=rogan, n_pairs=len(yy)),
        nowcast=nowcast,
        fixed={m: month_field(m, 'fixed') for m in sorted(_all_months()) if month_field(m, 'fixed')},
        sheet_shop={m: month_field(m, 'sheet_shop') for m in sorted(_all_months()) if month_field(m, 'sheet_shop')},
        params=dict(phi=PHI, k=f['k'], e_meta=e_meta, e_email=e_email, e_sms=e_sms, email_incr=0.6,
                    amz_slope=amz_slope, meta_slope=meta_slope, amz_beta=amz_beta, meta_beta=meta_beta, cr_season=f['s_cr'],
                    aov_season=f['aov_s']),
        backtest=dict(origins=origins, mape=mape, bias=bt_total_err, h_ape=h_ape, snaive_mape=st.mean(snaive_err)),
        promos=dict(lifts=EV.type_lifts(), amz_lifts=EV.amazon_lifts(), amazon_events=EV.AMZ_EV,
                    measured=[{k: v for k, v in (EV.measure(e) or {}).items() if k != 'by_month'} for e in EV.P if e['type'] not in EV.SEASONAL and EV.measure(e)],
                    calendar=[e for e in EV.P if e['end'] >= MONTHS[0] + '-01']),
        bias_corr=f['bias_corr'], fixed_level=fixed_level,
        flags=dict(rogan=sorted(ROGAN), blog_deindex=BLOG_DEINDEX,
                   signup_imports=[MONTHS[i] for i in range(CUT) if f['sig_mask'][i]]),
    )
    import os
    json.dump(out, open(os.environ.get('OUT', 'model_out.json'), 'w'), indent=1, default=float)

    print(f"Rolling backtest MAPE {mape:.1%} (seasonal naive {st.mean(snaive_err):.1%}), mean bias {bt_total_err:+.1%}")
    for o in origins: print(f"  origin {o['origin']}: MAPE {o['mape']:.0%} bias {o['bias']:+.0%}")
    print('  APE by horizon', [f"{v:.0%}" for v in h_ape])
    print(f"e_sms={e_sms:.2f} sms next3={[round(x) for x in f['sms_f'][:3]]} ss next3={f['ss_f'][:3]}")
    print(f"k={f['k']:.3f} cov={f['cov']:.3f} fin_ratio={fin_ratio:.3f} e_meta={e_meta:.2f} e_email={e_email:.2f}")
    print(f"beta amz {amz_beta:.2f} meta {meta_beta:.2f} gg {gg_beta:.2f} aa {aa_beta:.2f} | slopes amz {amz_slope:+.3f} meta {meta_slope:+.3f} gg {gg_slope:+.3f} aa {aa_slope:+.3f}")
    tot = {}
    for r in fut:
        y = r['month'][:4]
        tot.setdefault(y, [0, 0, 0])
        tot[y][0] += r['d2c']; tot[y][1] += r['amazon']; tot[y][2] += r['meta']
        print(f"  {r['month']} D2C {r['d2c']:>9,.0f} ord {r['orders']:>6,.0f} aov {r['aov']:>5.0f} "
              f"HA {r['ha_share']:.0%} AMZ {r['amazon']:>9,.0f} META {r['meta']:>8,.0f} email {r['email']:>7,.0f}")
    print(tot)
    print(f"cogs {cogs_pct:.3f} ad_ratio {ad_ratio:.3f} ddo {ddo_level:,.0f} e_brand {e_brand:.2f}/{e_brand_amz:.2f} brand slope {brand_slope:+.3f}")
    print('rogan', {k: (round(v['excess']) if isinstance(v, dict) else round(v, 1)) for k, v in rogan.items()})
    print('nowcast', nowcast and {k: round(fut[0][k]) for k in ('d2c', 'amazon', 'meta', 'google', 'amz_ads')})
    print('brand next', [round(x) for x in brand_f[:15]])


if __name__ == '__main__':
    main()
