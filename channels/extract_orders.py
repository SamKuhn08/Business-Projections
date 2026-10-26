"""Merge Shopify GraphQL order pages into channels/orders.json (deduped by createdAt + customer).

Sources: the existing orders.json, every page file in channels/raw/*.json, and (when run inside the Claude
session that pulled them) the session's saved tool results. Newer pulls overwrite older copies of an order,
so re-pulling the last ~35 days refreshes refunds, journeys and customer order counts.
Usage: python3 extract_orders.py [extra page files...]
"""
import json, glob, os, sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harvest import results

SESSION = '/root/.claude/projects/-home-claude/458d48e2-f7e7-5ae2-a433-b0949b223e75'


def load(f):
    t = open(f).read(); i = t.find('{')
    try: return json.loads(t[i:])
    except Exception:
        try: return json.loads(json.loads(t)[0]['text'])
        except Exception: return None


def key(n):
    cu = (n.get('u') or {}).get('i')
    return (n['c'], cu or ((n.get('a') or {}).get('s') or {}).get('a'))


def main():
    out = os.path.join(HERE, 'orders.json')
    seen = {key(n): n for n in (json.load(open(out)) if os.path.exists(out) else [])}
    files = sorted(glob.glob(f'{SESSION}/tool-results/mcp-Shopify-graphql_query-*.txt')) \
        + sorted(glob.glob(os.path.join(HERE, 'raw', '*.json'))) + sys.argv[1:]
    pages = [load(f) for f in files] + results(glob.glob(f'{SESSION}/subagents/*.jsonl'), 'endCursor')
    for r in pages:
        nodes = ((((r or {}).get('data') or {}).get('orders') or {}).get('nodes')) or []
        for n in nodes:
            if 'c' in n and 'j' in n: seen[key(n)] = n
    orders = sorted(seen.values(), key=lambda o: o['c'])
    json.dump(orders, open(out, 'w'), separators=(',', ':'))
    print(len(orders), orders[0]['c'] if orders else None, orders[-1]['c'] if orders else None)
    print(sorted(Counter(o['c'][:7] for o in orders).items()))


if __name__ == '__main__':
    main()
