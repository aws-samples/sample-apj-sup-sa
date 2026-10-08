"""Self-contained interactive HTML report.

The page embeds ``summary.json`` and filters it client-side (model, endpoint,
API, scope, region, prompt size, cache, tier) with plain JavaScript; no network
access and no external assets. All values are inserted with ``textContent``
(never ``innerHTML``), and the embedded JSON escapes ``<`` so it cannot close
the ``<script>`` element.
"""

from __future__ import annotations

import json
from pathlib import Path

_DIMS = ("display_name", "endpoint", "api", "scope", "region", "prompt_size", "cache")

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>Amazon Bedrock service-tier benchmark</title>
<style>
:root{--bg:#fff;--fg:#16191f;--mut:#5f6b7a;--line:#d5dbdb;--good:#037f0c;--bad:#d91515;--chip:#f2f3f3}
@media (prefers-color-scheme: dark){:root{--bg:#0f1b2a;--fg:#e9ebed;--mut:#9ba7b6;--line:#414d5c;--good:#29ad32;--bad:#ff5d64;--chip:#192534}}
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg);margin:24px}
h1{font-size:20px;margin:0 0 4px} .mut{color:var(--mut)} .filters{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
label{display:flex;flex-direction:column;font-size:12px;color:var(--mut)} select{min-width:140px;padding:4px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:4px}
table{border-collapse:collapse;width:100%;margin-top:8px} th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right;white-space:nowrap}
th:first-child,td:first-child,td.l{text-align:left} th{position:sticky;top:0;background:var(--bg);cursor:pointer}
.good{color:var(--good)} .bad{color:var(--bad)} .ns{color:var(--mut)} .chip{background:var(--chip);border-radius:4px;padding:1px 6px}
details{margin-top:16px} code{background:var(--chip);padding:1px 4px;border-radius:3px}
</style></head><body>
<h1>Amazon Bedrock service-tier benchmark</h1>
<div class="mut" id="meta"></div>
<details><summary>How to read this report</summary>
<p>Each row compares one tier against the <b>default</b> (Standard) tier in the same context: same model, endpoint, API, inference scope, region, prompt size and cache mode.
Δ is the tier's median (p50) minus default's median. <span class="good">Green</span> = faster, <span class="bad">red</span> = slower, grey = not significant (the 95% bootstrap confidence interval includes zero).</p>
<p><b>TTFT</b> time to first token (including reasoning) · <b>TTFAT</b> time to first answer token · <b>E2E</b> end-to-end latency · <b>ITL</b> inter-token latency ((E2E−TTFT)/(output tokens−1)).
<b>Burst</b> = share of samples whose stream arrived all at once after queueing (decode under 5% of E2E); for those, TTFT is close to E2E and ITL does not measure decode speed. Cold samples that read from cache, warm samples that missed the cache, and samples served on a different tier are excluded and counted.</p>
</details>
<div class="filters" id="filters"></div>
<h2>Tier comparison</h2><div class="mut" id="ccount"></div>
<table id="cmp"><thead></thead><tbody></tbody></table>
<h2>Cells</h2><div class="mut" id="xcount"></div>
<table id="cells"><thead></thead><tbody></tbody></table>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
"use strict";
var D=JSON.parse(document.getElementById("data").textContent);
var DIMS=__DIMS__; var TIER="tier";
var meta=D.meta||{};
document.getElementById("meta").textContent="Run "+(meta.run_id||"")+" · "+(meta.started||"")+" → "+(meta.finished||"")+" · v"+(meta.version||"")+(meta.reasoning_effort?(" · reasoning_effort="+meta.reasoning_effort):"");
function el(t,txt,cls){var e=document.createElement(t);if(txt!==undefined&&txt!==null)e.textContent=String(txt);if(cls)e.className=cls;return e}
function ctxOf(c){var o={display_name:c.display_name};for(var k in c.context)o[k]=c.context[k];o.tier=c.tier;return o}
var rowsC=(D.comparisons||[]).map(function(c){return {dims:ctxOf(c),c:c}});
var rowsX=(D.cells||[]).map(function(s){return {dims:s.cell,s:s}});
var state={};
function uniq(key){var set={};rowsX.forEach(function(r){var v=r.dims[key];if(v!==undefined&&v!==null)set[v]=1});return Object.keys(set).sort()}
var F=document.getElementById("filters");
DIMS.concat([TIER]).forEach(function(k){var lab=el("label",k.replace("_"," "));var sel=el("select");sel.appendChild(el("option","(all)"));uniq(k).forEach(function(v){var o=el("option",v);o.value=v;sel.appendChild(o)});sel.addEventListener("change",function(){state[k]=sel.value==="(all)"?null:sel.value;render()});lab.appendChild(sel);F.appendChild(lab)});
function pass(d){for(var k in state){if(state[k]&&String(d[k])!==state[k])return false}return true}
function ms(v){return v===null||v===undefined?"—":(v*1000).toFixed(0)}
function dcell(d,lowerBetter,scale,unit){var td=el("td");if(!d||d.delta===null||d.delta===undefined){td.textContent="—";return td}
var v=d.delta*scale;td.textContent=(v>0?"+":"")+v.toFixed(scale===1?1:0)+" "+unit+(d.pct!==null&&d.pct!==undefined?(" ("+(d.pct>0?"+":"")+d.pct.toFixed(0)+"%)"):"");
if(!d.significant){td.className="ns";td.title="95% CI "+(d.ci_low*scale).toFixed(0)+" .. "+(d.ci_high*scale).toFixed(0)+" (includes 0)";return td}
var better=lowerBetter?d.delta<0:d.delta>0;td.className=better?"good":"bad";td.title="95% CI "+(d.ci_low*scale).toFixed(1)+" .. "+(d.ci_high*scale).toFixed(1);return td}
function head(tbl,cols){var th=tbl.tHead;th.textContent="";var tr=el("tr");cols.forEach(function(c){tr.appendChild(el("th",c))});th.appendChild(tr)}
head(document.getElementById("cmp"),["Model","Endpoint","API","Scope","Region","Size","Cache","Tier","Default TTFT p50","ΔTTFT","ΔTTFAT","ΔE2E","ΔITL","ΔOutput tok/s"]);
head(document.getElementById("cells"),["Cell","n","TTFT p50","TTFT p90","TTFT p99","TTFAT p50","E2E p50","E2E p90","ITL p50","Out tok/s p50","In tokens","Out tokens","Cache hit","Burst","Served","Excluded","Errors"]);
function render(){
var tb=document.querySelector("#cmp tbody");tb.textContent="";var n=0;
rowsC.forEach(function(r){if(!pass(r.dims))return;n++;var c=r.c,d=c.deltas,x=c.context,tr=el("tr");
[c.display_name,x.endpoint,x.api,x.scope,x.region,x.prompt_size,x.cache].forEach(function(v,i){tr.appendChild(el("td",v,i===0?"l":"l"))});
tr.appendChild(el("td",c.tier,"l"));tr.appendChild(el("td",ms(d.ttft.default_p50)));
tr.appendChild(dcell(d.ttft,true,1000,"ms"));tr.appendChild(dcell(d.ttfat,true,1000,"ms"));tr.appendChild(dcell(d.e2e,true,1000,"ms"));tr.appendChild(dcell(d.itl,true,1000,"ms"));tr.appendChild(dcell(d.output_tps,false,1,"tok/s"));tb.appendChild(tr)});
document.getElementById("ccount").textContent=n+" comparison(s)";
var tb2=document.querySelector("#cells tbody");tb2.textContent="";var m=0;
rowsX.forEach(function(r){if(!pass(r.dims))return;m++;var s=r.s,st=s.stats,tr=el("tr");tr.appendChild(el("td",s.cell.label,"l"));
tr.appendChild(el("td",st.e2e.n));[st.ttft.p50,st.ttft.p90,st.ttft.p99,st.ttfat.p50,st.e2e.p50,st.e2e.p90,st.itl.p50].forEach(function(v){tr.appendChild(el("td",ms(v)))});
tr.appendChild(el("td",st.output_tps.p50===null?"—":st.output_tps.p50.toFixed(0)));
tr.appendChild(el("td",s.tokens.input_tokens===null?"—":Math.round(s.tokens.input_tokens)));tr.appendChild(el("td",s.tokens.output_tokens===null?"—":Math.round(s.tokens.output_tokens)));
tr.appendChild(el("td",s.cache_hit_rate===null?"—":Math.round(s.cache_hit_rate*100)+"%"));
tr.appendChild(el("td",s.burst_share===null||s.burst_share===undefined?"—":Math.round(s.burst_share*100)+"%"));tr.appendChild(el("td",JSON.stringify(s.served_tiers),"l"));tr.appendChild(el("td",JSON.stringify(s.excluded),"l"));tr.appendChild(el("td",JSON.stringify(s.errors),"l"));tb2.appendChild(tr)});
document.getElementById("xcount").textContent=m+" cell(s)";}
render();
})();
</script></body></html>
"""


def _embed(obj: object) -> str:
    """JSON safe to place inside a ``<script type="application/json">`` element."""
    return (
        json.dumps(obj, default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render(summary: dict) -> str:
    return _TEMPLATE.replace("__DIMS__", json.dumps(list(_DIMS))).replace(
        "__DATA__", _embed(summary)
    )


def write_html(summary_json_path: Path, out_path: Path | None = None) -> Path:
    """Render ``summary.json`` into a self-contained ``report.html``."""
    summary = json.loads(Path(summary_json_path).read_text(encoding="utf-8"))
    out = out_path or Path(summary_json_path).with_name("report.html")
    tmp = out.with_name(f".{out.name}.tmp")
    tmp.write_text(render(summary), encoding="utf-8")
    tmp.replace(out)
    return out
