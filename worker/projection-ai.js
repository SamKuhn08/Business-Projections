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
${followup ? 'The levers you asked for have now been applied and the data below already reflects them. Answer the question with the new numbers and set "levers" to null.' : 'If you set "levers", the dashboard will apply them and ask you again with the recomputed numbers, so keep "text" to one short line in that case.'}
Respond with STRICT JSON only, no code fences, exactly: {"text":"...","range":null,"compare":null,"levers":null}`;

    const user = `${String(context).slice(0, 150000)}\n\nQuestion: ${String(question).slice(0, 2000)}`;

    try {
      let out, used, lastErr;
      for (const m of MODELS) {
        try {
          out = await env.AI.run(m, { messages: [{ role: 'system', content: sys }, { role: 'user', content: user }], max_tokens: 2500, temperature: 0.1 });
          used = m;
          if (textOf(out)) break;
        } catch (e) { lastErr = e; }
      }
      if (!out) throw lastErr || new Error('no model answered');
      let raw = textOf(out);
      let p;
      if (raw && typeof raw === 'object') p = raw;
      else {
        raw = String(raw).trim().replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '').trim();
        try { p = JSON.parse(raw); }
        catch { const m = raw.match(/\{[\s\S]*\}/); try { p = m ? JSON.parse(m[0]) : { text: raw }; } catch { p = { text: raw }; } }
      }
      const ym = v => typeof v === 'string' && /^\d{4}-\d{2}$/.test(v);
      const clean = {
        text: typeof p.text === 'string' ? p.text : String(p.text ?? raw ?? ''),
        range: Array.isArray(p.range) && p.range.length === 2 && p.range.every(ym) ? (p.range[0] <= p.range[1] ? p.range : [p.range[1], p.range[0]]) : null,
        compare: ['off', 'prev', 'ly', 'plan'].includes(p.compare) ? p.compare : null,
        levers: null,
      };
      if (!followup && p.levers && typeof p.levers === 'object') {
        const l = {};
        for (const k of LEVERS) {
          const v = Number(p.levers[k]);
          if (Number.isFinite(v) && v !== 0) l[k] = Math.max(k === 'blog' ? -100 : -20, Math.min(k === 'blog' ? 100 : 20, Math.round(v / 5) * 5));
        }
        if (Object.keys(l).length) clean.levers = l;
      }
      clean.model = used;
      return j(clean, 200, cors);
    } catch (e) {
      return j({ error: 'AI error: ' + (e && e.message ? e.message : e) }, 500, cors);
    }
  },
};

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
