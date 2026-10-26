"""Pull ShopifyQL / GraphQL tool results out of Claude session transcripts (jsonl) by query substring."""
import json, sys, glob
def results(paths, needle):
    out = []
    for p in paths:
        for line in open(p):
            if needle not in line: continue
            try: o = json.loads(line)
            except Exception: continue
            c = o.get('message', {}).get('content')
            if not isinstance(c, list): continue
            for part in c:
                if part.get('type') != 'tool_result': continue
                cont = part.get('content')
                txt = cont if isinstance(cont, str) else ''.join(x.get('text', '') for x in cont if isinstance(x, dict))
                i = txt.find('{')
                if i < 0: continue
                try: out.append(json.loads(txt[i:]))
                except Exception: pass
    return out
