"""Local browser UI for the live federated-query demo (M5 / M9).

Consolidated onto the engine's HTTP seam: this serves one page from the *same*
FastAPI app that exposes ``POST /federate`` (:mod:`cdf.service.app`), and the
page calls that endpoint. No query wiring lives here — the service owns it
(:func:`cdf.service.app.FederationService.from_env`, credentials-in-engine per
CC-7). The page just renders the grounded envelope: the per-source partition,
the joined answer, and the citations (actual SQL/AQL, source objects, as-of),
with the cite-or-refuse status.

    .venv/bin/python deploy/demo/server.py      # then open http://localhost:8099
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.responses import HTMLResponse

from cdf.service.app import FederationService, create_app

# Turnkey defaults matching the two deploy/ stacks (explicit env always wins).
_REPO = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency). Populates any var not already
    exported — so a key placed in a .env (e.g. NL2SPARQL_API_KEY / OPENAI_API_KEY
    to enable the NL front-end) is picked up, while an explicit `export` wins."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


# CDF_ENV_FILE overrides the location; otherwise <cdf-repo>/.env.
_load_dotenv(Path(os.environ.get("CDF_ENV_FILE", str(_REPO / ".env"))))

os.environ.setdefault("CDF_CSI_DIR", str(_REPO / "deploy" / "csi"))
os.environ.setdefault("CDF_PREPARED_QUESTIONS", str(_REPO / "deploy" / "questions.json"))
os.environ.setdefault("ONTOP_SPARQL_ENDPOINT", "http://localhost:8090/sparql")
os.environ.setdefault("ARANGO_URL", "http://localhost:8530")
os.environ.setdefault("ARANGO_DB", "cmf")
os.environ.setdefault("ARANGO_USER", "root")
os.environ.setdefault("ARANGO_PASSWORD", "cdf")

# A real cross-source JOIN (on the account_id business key) — the demo query.
# Vocabulary matches the r2g-GENERATED CSI/R2RML (WP-P1.2): snake_case table
# and column names (c:accounts / c:account_name), not the retired hand-authored
# concepts.
DEFAULT_SPARQL = """PREFIX c: <urn:arango-sparql:concept#>
SELECT ?name ?tier ?source ?url WHERE {
  ?acc a c:accounts ; c:account_name ?name ; c:current_product_tier ?tier ; c:account_id ?aid .
  ?d   a c:Document ; c:source ?source     ; c:citable_url ?url ; c:account_id ?aid .
}"""

_SERVICE = FederationService.from_env()
app = create_app(_SERVICE)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    import json as _json

    questions = _json.dumps(sorted(_SERVICE.prepared_questions))
    return PAGE.replace("__SPARQL__", DEFAULT_SPARQL).replace("__QUESTIONS__", questions)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CDF — Federated Query (live)</title>
<style>
  :root {
    --bg:#f6f7f9; --panel:#fff; --ink:#1c2024; --muted:#6b7280; --line:#e5e7eb;
    --pg:#2563eb; --ar:#059669; --accent:#7c3aed; --code:#f3f4f6; --codeink:#111827;
    --ok:#059669; --refuse:#dc2626; --partial:#d97706;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1115; --panel:#171a21; --ink:#e6e8eb; --muted:#9aa4b2; --line:#262b34;
      --code:#0c0e12; --codeink:#d6dae0; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:1000px; margin:0 auto; padding:28px 20px 80px; }
  h1 { font-size:20px; margin:0 0 2px; }
  .sub { color:var(--muted); margin:0 0 22px; font-size:13.5px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:16px 18px; margin:14px 0; }
  textarea { width:100%; min-height:120px; resize:vertical; border:1px solid var(--line);
    border-radius:8px; background:var(--code); color:var(--codeink); padding:12px;
    font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
  button { background:var(--accent); color:#fff; border:0; border-radius:8px;
    padding:10px 18px; font-size:14px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.6; cursor:default; }
  .chip { background:#eef0f6; color:#333; border:1px solid var(--line); border-radius:16px;
    padding:6px 12px; font-size:12.5px; font-weight:500; cursor:pointer; }
  .chip:hover { background:#e2e6f0; }
  .row { display:flex; gap:10px; align-items:center; margin-top:10px; flex-wrap:wrap; }
  label.chk { color:var(--muted); font-size:13px; display:flex; gap:6px; align-items:center; }
  .badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px;
    font-weight:700; letter-spacing:.02em; }
  .b-ok { background:color-mix(in srgb,var(--ok) 18%,transparent); color:var(--ok); }
  .b-refuse { background:color-mix(in srgb,var(--refuse) 18%,transparent); color:var(--refuse); }
  .b-partial { background:color-mix(in srgb,var(--partial) 18%,transparent); color:var(--partial); }
  .b-failed { background:color-mix(in srgb,var(--refuse) 14%,transparent); color:var(--refuse); }
  .src { display:inline-block; padding:1px 8px; border-radius:6px; font-size:11.5px;
    font-weight:700; }
  .src-postgresql { background:color-mix(in srgb,var(--pg) 16%,transparent); color:var(--pg); }
  .src-arango { background:color-mix(in srgb,var(--ar) 16%,transparent); color:var(--ar); }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
    margin:22px 2px 6px; }
  pre { background:var(--code); color:var(--codeink); border-radius:8px; padding:12px;
    overflow-x:auto; font:12.5px/1.5 ui-monospace,Menlo,monospace; margin:8px 0 0; }
  table { width:100%; border-collapse:collapse; margin-top:6px; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); font-size:14px; }
  th { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .flow { display:flex; gap:8px; align-items:center; color:var(--muted); font-size:12.5px;
    flex-wrap:wrap; margin-top:4px; }
  details summary { cursor:pointer; color:var(--muted); font-size:12.5px; }
  .meta { color:var(--muted); font-size:12.5px; }
  .err { color:var(--refuse); }
</style></head>
<body><div class="wrap">
  <h1>Contextual Data Fabric — Federated Query</h1>
  <p class="sub">Ask a question in English (or run conceptual SPARQL). It's partitioned by source,
     run live across <b>Postgres</b> (via Ontop, SPARQL&rarr;SQL) and <b>ArangoDB</b>
     (via arango-sparql-py, SPARQL&rarr;AQL), joined on a business key, cited — no data moved.
     Served by the engine's <code>POST /federate</code> seam.</p>

  <div class="card">
    <label for="nlq" style="font-weight:600">Ask a question</label>
    <input id="nlq" type="text" placeholder="Type a question, or click an example below…"
           style="width:100%;margin:8px 0;padding:10px;border:1px solid var(--line);border-radius:8px;font-size:15px">
    <div id="examples" class="row" style="flex-wrap:wrap;gap:6px"></div>
    <div class="row" style="margin-top:10px">
      <button id="ask" onclick="ask()">Ask</button>
      <label class="chk"><input type="checkbox" id="ap"> allow partial (concierge mode)</label>
      <span id="status"></span>
    </div>
    <details style="margin-top:12px">
      <summary style="cursor:pointer;color:var(--muted)">Advanced: edit the conceptual SPARQL directly</summary>
      <textarea id="q" style="margin-top:8px">__SPARQL__</textarea>
      <div class="row"><button id="run" onclick="run()">Run SPARQL</button></div>
    </details>
  </div>

  <div id="out"></div>

<script>
const el = (h) => { const d=document.createElement('div'); d.innerHTML=h; return d.firstElementChild; };
const esc = (s) => (s==null?'':(''+s)).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const srcTag = (kind, id) => `<span class="src src-${esc(kind)}">${esc(id)}</span>`;

const EXAMPLES = __QUESTIONS__;

function renderExamples() {
  const box = document.getElementById('examples');
  EXAMPLES.forEach(q => {
    const chip = el(`<button class="chip" type="button">${esc(q)}</button>`);
    chip.onclick = () => { document.getElementById('nlq').value = q; ask(); };
    box.appendChild(chip);
  });
}

async function post(payload, out, stat) {
  stat.innerHTML='running…'; out.innerHTML='';
  try {
    const r = await fetch('/federate', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({...payload, allow_partial: document.getElementById('ap').checked})});
    const d = await r.json();
    stat.innerHTML='';
    if (!r.ok) { out.appendChild(el(`<div class="card err"><b>${r.status}:</b> ${esc(d.detail||JSON.stringify(d))}</div>`)); return; }
    render(d, out);
  } catch(e) { stat.innerHTML=`<span class="err">${esc(''+e)}</span>`; }
}

async function ask() {
  const q = document.getElementById('nlq').value.trim();
  if (!q) return;
  const btn=document.getElementById('ask');
  btn.disabled=true;
  await post({question:q}, document.getElementById('out'), document.getElementById('status'));
  btn.disabled=false;
}

async function run() {
  const btn=document.getElementById('run');
  btn.disabled=true;
  await post({sparql: document.getElementById('q').value},
             document.getElementById('out'), document.getElementById('status'));
  btn.disabled=false;
}

function render(d, out) {
  const bmap={grounded:'b-ok',refused:'b-refuse',partial:'b-partial'};
  out.appendChild(el(`<div class="row" style="margin:6px 2px 0">
     <span class="badge ${bmap[d.status]||''}">${esc(d.status)}</span>
     ${d.refusal_reason?`<span class="err">${esc(d.refusal_reason)}</span>`:''}</div>`));

  // Partition / execution — one card per leg (from the retrieval path).
  out.appendChild(el('<h2>How it was answered — partition by source</h2>'));
  (d.retrieval_path||[]).forEach(s => {
    const c = el(`<div class="card"></div>`);
    c.appendChild(el(`<div class="flow">${srcTag(s.kind, s.source_id)}
       <span class="badge ${s.status==='ok'?'b-ok':'b-failed'}">${esc(s.status)}</span>
       <span>· ${s.row_count} rows</span>
       ${s.error?`<span class="err">${esc(s.error)}</span>`:''}</div>`));
    if (s.sparql) c.appendChild(el(`<pre>${esc(s.sparql)}</pre>`));
    out.appendChild(c);
  });

  // Answer.
  out.appendChild(el('<h2>Answer — joined live across both sources</h2>'));
  const rows = d.bindings||[];
  if (!rows.length) out.appendChild(el(`<div class="card meta">no rows</div>`));
  else {
    const cols = [...new Set(rows.flatMap(Object.keys))];
    out.appendChild(el(`<div class="card" style="overflow-x:auto"><table><thead><tr>${
      cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${
      rows.map(r=>`<tr>${cols.map(c=>`<td>${esc(r[c])}</td>`).join('')}</tr>`).join('')
      }</tbody></table></div>`));
  }

  // Citations.
  out.appendChild(el('<h2>Citations — provenance for every grounded leg</h2>'));
  (d.citations||[]).forEach(c => {
    const card = el(`<div class="card"></div>`);
    card.appendChild(el(`<div class="flow">${srcTag(c.kind, c.source_id)}
       <span>objects: <b>${(c.source_objects||[]).map(esc).join(', ')||'—'}</b></span>
       <span>· ${c.row_count} rows</span>
       <span>· as-of ${esc(c.as_of||'—')}</span></div>`));
    card.appendChild(el(`<details><summary>actual query executed (${esc(c.kind)})</summary>
       <pre>${esc(c.native_query||'')}</pre></details>`));
    out.appendChild(card);
  });
}
document.getElementById('nlq').addEventListener('keydown', e => { if (e.key==='Enter') ask(); });
renderExamples();
</script>
</div></body></html>
"""


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("CDF_UI_PORT", "8099"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
