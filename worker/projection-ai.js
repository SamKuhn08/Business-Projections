/*  Iron Neck revenue projection: "Ask" backend (Cloudflare Worker + Workers AI)
    Worker name: ironneck-projection-ai. Needs a Workers AI binding named "AI".
    The dashboard sends its own numbers with every question (monthly actuals and
    projections, the selected range and comparison, lever settings, model notes),
    so the model only ever answers from what the page is showing.              */

const MODELS = ['@cf/openai/gpt-oss-120b', '@cf/meta/llama-3.3-70b-instruct-fp8-fast'];   // first that answers wins
const ALLOWED = [/^https:\/\/ironneck-revenue-projection\.netlify\.app$/, /^https:\/\/[a-z0-9-]+--ironneck-revenue-projection\.netlify\.app$/, /^http:\/\/localhost(:\d+)?$/];
const LEVERS = ['meta', 'google', 'brand', 'cr', 'aov', 'email', 'sms', 'blog', 'amazon', 'ddo'];

export default {
  async fetch(req, env) {
    const origin = req.headers.get('Origin') || '';
    const cors = {
      'Access-Control-Allow-Origin': ALLOWED.some(r => r.test(origin)) ? origin : 'https://ironneck-revenue-projection.netlify.app',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Vary': 'Origin',
    };
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (req.method !== 'POST') return j({ error: 'POST only' }, 405, cors);
    if (Number(req.headers.get('Content-Length') || 0) > 200000) return j({ error: 'request too large' }, 413, cors);

    let body;
    try { body = await req.json(); } catch { return j({ error: 'bad json' }, 400, cors); }
    const { question, context, followup } = body || {};
    if (!question || !context) return j({ error: 'question and context required' }, 400, cors);

    const sys = `You are the revenue analyst for Iron Neck, a neck-training equipment brand. You answer questions about its revenue projection dashboard.
You receive the dashboard's own data: one row per month (actuals from the finance books, then the model's projection through Dec 2027), the date range the user has selected and what it is compared with, the scenario lever settings, and notes on how the model works.
Rules:
- Use ONLY the numbers provided. Quote them ($, %, months). Never invent figures.
- For any year, quarter or the selected range, use the PRECOMPUTED TOTALS section; do not add monthly rows yourself unless the period is not listed there.
- "proj" rows are projections, "partial" is the month in progress (actuals to date plus projection), "actual" rows are booked.
- Be concise: 2 to 6 short sentences, plain text, no markdown. Lead with the answer.
- Explain drivers using the model notes when asked why (trend, seasonality, product mix, brand search, channels).
- If the data cannot answer, say so in one sentence.
You can also change what the dashboard shows:
- "range": ["YYYY-MM","YYYY-MM"] to select the months the question is about (inclusive), else null.
- "compare": one of "off","prev","ly","plan" to set the comparison, else null.
- "levers": only when the user asks a what-if (e.g. "what if Meta spend rises 20%"), an object of lever percentages to set, keys from ${LEVERS.join(', ')}, values between -20 and 20 (blog: -100 to 100). Otherwise null.
  Lever meanings: meta / google = ad spend change, brand = branded search volume, cr = Shopify conversion rate, aov = Shopify average order value, email / sms = email or SMS revenue, blog = organic blog traffic, amazon = Amazon revenue, ddo = D/D/O revenue.
  ${followup ? '' : 'For ANY what-if you MUST return "levers" and must NOT estimate the impact yourself: the dashboard model computes it. Example: {"text":"Applying Meta +20% and recalculating.","range":["2027-01","2027-12"],"compare":null,"levers":{"meta":20}}. If the scenario lies outside the lever limits, use the nearest limit and say so.'}
${followup ? 'The levers you asked for have now been applied and the data below already reflects them. Answer the question with the new numbers and set "levers" to null.' : 'If you set "levers", the dashboard will apply them and ask you again with the recomputed numbers, so keep "text" to one short line in that case.'}
Respond with STRICT JSON only, no code fences, exactly: {"text":"...","range":null,"compare":null,"levers":null}`;

    const ctx = String(context).slice(0, 150000);
    const q = String(question).slice(0, 2000);
    const user = `${ctx}\n\nQuestion: ${q}`;

    try {
      let pp = {};
      // Step 1 (first call only): a small planning call that only decides range / compare / levers.
      // What-ifs return here so the dashboard recomputes and calls back with followup=true.
      if (!followup) {
        const head = ctx.split('\n').slice(0, 6).join('\n');
        const plan = `Decide how an Iron Neck revenue dashboard should react to a question. Do NOT answer it.
Dashboard context (first lines): ${head}
Months run Jan 2023 to Dec 2027. Levers (percent changes to the projection): ${LEVERS.join(', ')}. meta / google = ad spend, brand = branded search volume, cr = Shopify conversion rate, aov = Shopify average order value, email / sms = email or SMS revenue, blog = organic blog traffic, amazon = Amazon revenue, ddo = D/D/O revenue. Limits -20..20 (blog -100..100).
Return STRICT JSON only: {"whatif":true|false,"levers":{...}|null,"range":["YYYY-MM","YYYY-MM"]|null,"compare":"off"|"prev"|"ly"|"plan"|null}
- whatif is true when the question asks what would happen if something changed (spend, conversion, AOV, traffic, channel revenue). Then levers holds the matching lever percentages.
- range: the months the question is about, if it names a period (a year = Jan to Dec of that year, a quarter = its 3 months). Else null.
- compare: "ly" for vs last year, "plan" for vs plan/goal/target, "prev" for vs previous period. Else null.
Question: ${q}`;
        pp = parse(await run(env, [{ role: 'user', content: plan }], 600).catch(() => ''));
        const pl = levers(pp.levers);
        if (pp.whatif && pl) {
          const nice = Object.entries(pl).map(([k, v]) => `${k} ${v > 0 ? '+' : ''}${v}%`).join(', ');
          return j({ text: `Applying ${nice} and recalculating.`, range: rng(pp.range), compare: cmp(pp.compare), levers: pl, model: 'planner' }, 200, cors);
        }
      }

      // Step 2: the answer.
      const r = await run(env, [{ role: 'system', content: sys }, { role: 'user', content: user }], 2500, true);
      const p = parse(r.text);
      const clean = {
        text: typeof p.text === 'string' ? p.text : String(p.text ?? r.text ?? ''),
        range: rng(pp.range) || rng(p.range),
        compare: cmp(pp.compare) || cmp(p.compare),
        levers: followup ? null : levers(p.levers),
        model: r.model,
      };
      clean.text = clean.text.replace(/\$-/g, '-$');
      return j(clean, 200, cors);
    } catch (e) {
      return j({ error: 'AI error: ' + (e && e.message ? e.message : e) }, 500, cors);
    }
  },
};

async function run(env, messages, max_tokens, withModel) {
  let lastErr;
  for (const m of MODELS) {
    try {
      const out = await env.AI.run(m, { messages, max_tokens, temperature: 0.1 });
      const t = textOf(out);
      if (t) return withModel ? { text: t, model: m } : t;
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error('no model answered');
}

function parse(raw) {
  if (raw && typeof raw === 'object') return raw;
  const s = String(raw || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '').trim();
  try { return JSON.parse(s); } catch {}
  const m = s.match(/\{[\s\S]*\}/);
  try { return m ? JSON.parse(m[0]) : { text: s }; } catch { return { text: s }; }
}

const ym = v => typeof v === 'string' && /^\d{4}-\d{2}$/.test(v);
function rng(r) { return Array.isArray(r) && r.length === 2 && r.every(ym) ? (r[0] <= r[1] ? r : [r[1], r[0]]) : null; }
function cmp(c) { return ['off', 'prev', 'ly', 'plan'].includes(c) ? c : null; }
function levers(o) {
  if (!o || typeof o !== 'object') return null;
  const l = {};
  for (const k of LEVERS) {
    const v = Number(o[k]);
    if (Number.isFinite(v) && v !== 0) l[k] = Math.max(k === 'blog' ? -100 : -20, Math.min(k === 'blog' ? 100 : 20, Math.round(v / 5) * 5));
  }
  return Object.keys(l).length ? l : null;
}

// Workers AI shapes: string, {response}, chat completions {choices}, or Responses API {output:[{type:'message',content:[{type:'output_text',text}]}]}
function textOf(out) {
  if (!out) return '';
  if (typeof out === 'string') return out;
  if (out.response != null) return out.response;
  const c = out.choices?.[0]?.message?.content; if (c) return c;
  if (Array.isArray(out.output)) {
    const t = out.output.filter(o => o.type === 'message').flatMap(o => o.content || []).filter(c => c.type === 'output_text' || c.text).map(c => c.text).join('');
    if (t) return t;
  }
  return out.output_text || out.result || '';
}

function j(o, status, h) {
  return new Response(JSON.stringify(o), { status, headers: { ...h, 'Content-Type': 'application/json' } });
}
