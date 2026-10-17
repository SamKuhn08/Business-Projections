"""Turn a text export of the Iron Neck Operations & Finance 'Targets' tab into raw files for refresh.py.

Usage:  python3 targets_parse.py EXPORT_FILE RAW_DIR LAST_CLOSED(YYYY-MM)

EXPORT_FILE: the spreadsheet exported as text, with a line '## Sheet name: Targets' followed by the tab's CSV rows
(the format claude.ai uploads use), or a plain CSV of just the Targets tab.
Writes RAW_DIR/targets.json (actuals, tgt_* fields, months <= LAST_CLOSED) and RAW_DIR/plan.json (the 'Est.' columns).

Tab layout: one block per year. Left side: 'Financials x Channel | Full Year | Partial Year | <year> | January ... December'
then rows labelled in column D (Shopify, AMZ, D/D/O, Total, COGS, Ad Spend, Other SMA, Payroll, G&A, Other, Net Income).
Right side of the current year: '<year> | January Est. | January | Delta | February Est. | ...'.
The stale 'Partial Year' copies at the top of the tab (year label in column C) are ignored.
"""
import csv, io, json, os, re, sys

KEY = {'Shopify': 'shop', 'AMZ': 'amz', 'D/D/O': 'ddo', 'Total': 'total', 'COGS': 'cogs', 'Ad Spend': 'ad',
       'Other SMA': 'other_sma', 'Payroll': 'payroll', 'G&A': 'ga', 'Other': 'other', 'Net Income': 'ni'}
PLAN = {'Shopify': 'goal_shop', 'AMZ': 'goal_amz', 'D/D/O': 'goal_ddo', 'Net Income': 'goal_ni'}


def num(s):
    s = (s or '').strip().replace('$', '').replace(',', '')
    if s in ('', '-'): return None
    neg = s.startswith('-'); s = s.lstrip('-')
    try: v = float(s)
    except ValueError: return None
    return round(-v if neg else v)


def rows_of(path):
    txt = open(path, encoding='utf-8-sig').read()
    L = txt.splitlines()
    if any(l.strip() == '## Sheet name: Targets' for l in L):
        a = next(i for i, l in enumerate(L) if l.strip() == '## Sheet name: Targets') + 1
        b = next((i for i in range(a, len(L)) if L[i].startswith('## Sheet name:')), len(L))
        L = L[a:b]
    return [r + [''] * 80 for r in csv.reader(io.StringIO("\n".join(L)))]


def parse(path, last_closed):
    rows = rows_of(path)
    act, plan = {}, {}
    year = None
    pyear, pcol = None, None
    for r in rows:
        if re.fullmatch(r'20\d\d', r[3].strip()) and r[4].strip() == 'January':
            year = int(r[3])
        if 'January Est.' in r:
            j = r.index('January Est.')
            lab = r[j - 1].strip()
            pyear, pcol = (int(lab), j) if re.fullmatch(r'20\d\d', lab) else (None, None)
        lab = r[3].strip()
        if year and lab in KEY:
            for mi in range(12):
                m = f'{year}-{mi + 1:02d}'
                v = num(r[4 + mi])
                if v is not None and m <= last_closed:
                    act.setdefault(m, {}).setdefault('tgt_' + KEY[lab], v)     # first block for a year wins
        if pyear and pcol and r[pcol - 1].strip() in PLAN:
            for mi in range(12):
                v = num(r[pcol + 3 * mi])
                if v is not None:
                    plan.setdefault(f'{pyear}-{mi + 1:02d}', {}).setdefault(PLAN[r[pcol - 1].strip()], v)
    return act, plan


if __name__ == '__main__':
    src, raw, last = sys.argv[1], sys.argv[2], sys.argv[3]
    act, plan = parse(src, last)
    os.makedirs(raw, exist_ok=True)
    json.dump(act, open(os.path.join(raw, 'targets.json'), 'w'), indent=1, sort_keys=True)
    json.dump(plan, open(os.path.join(raw, 'plan.json'), 'w'), indent=1, sort_keys=True)
    print(f'targets: {len(act)} months ({min(act)} to {max(act)}); plan: {len(plan)} months')
