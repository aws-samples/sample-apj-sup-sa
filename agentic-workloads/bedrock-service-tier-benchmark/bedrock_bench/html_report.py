"""Render a friendly, self-contained HTML report from a run's ``summary.json``.

Design goals:
* **Self-contained** — inline CSS, no CDN/JS dependencies; opens offline anywhere.
* **Readable for non-experts** — a glossary explains every metric/abbreviation,
  values are shown in milliseconds, and flex-vs-default deltas are colour-coded
  (green = flex faster, red = flex slower) for both the median and the tail.
* **Honest** — surfaces served-tier provenance, error counts, and an explicit
  caveat about percentile precision at small n.

Consumes the structured ``summary.json`` (not the markdown) so numbers are exact.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

#: Percentiles shown in the HTML views.
_PCTS = ("p20", "p50", "p90")

#: Non-default tiers, each compared against default. Order = display order.
_COMPARE_TIERS = ("flex", "priority")

# Plain-language glossary. (term, short, long)
GLOSSARY = [
    (
        "TTFT",
        "Time To First Token",
        "How long after sending the request until the very first piece of the "
        "answer arrives. This is what a user perceives as responsiveness — lower is better.",
    ),
    (
        "Total latency",
        "Total response time",
        "How long until the full answer has finished streaming (time to last token). "
        "Depends partly on how many tokens the model chose to generate.",
    ),
    (
        "Default tier",
        "Standard service tier",
        "Bedrock's normal pay-per-token tier, tuned for consistent everyday latency. "
        "Selected by not requesting any special tier.",
    ),
    (
        "Flex tier",
        "Flex service tier",
        "A lower-cost Bedrock tier for latency-tolerant workloads (batch, evals, "
        "agents). You trade some speed — usually in the worst-case tail — for price.",
    ),
    (
        "Priority tier",
        "Priority service tier",
        "A premium Bedrock tier that gets preferential processing (prioritised over "
        "standard and flex), for a higher price. Expected to be as fast or faster "
        "than default.",
    ),
    (
        "p20 / p50 / p90",
        "Percentiles",
        "The value below which that % of requests fell. p50 is the median (typical "
        "request); p90 describes the slow tail (only 1 request in 10 was slower).",
    ),
    (
        "Δp50",
        "Tier-vs-default delta (median)",
        "Percent change of a tier (flex or priority) relative to default at the "
        "median. Negative (green) = that tier was faster; positive (red) = slower. "
        "Shown separately for flex and for priority, always against default.",
    ),
    (
        "NA",
        "Not available",
        "The model does not serve that tier on that transport, so there is no "
        "measurement to compare.",
    ),
    (
        "InvokeModel",
        "bedrock-runtime transport",
        "The classic AWS SDK streaming API (InvokeModelWithResponseStream), "
        "authenticated with IAM/SigV4.",
    ),
    (
        "Mantle",
        "bedrock-mantle transport",
        "Bedrock's OpenAI-compatible endpoint, authenticated with a bearer token. "
        "Same models, different front door — it tends to add a small fixed overhead.",
    ),
    (
        "served tier",
        "Tier actually used",
        "The tier Bedrock reported it actually served the request with. It can "
        "differ from what was requested; here every flex request was honored. "
        "Default on InvokeModel is '(unreported)' because Standard sends no tier header.",
    ),
    (
        "n",
        "Sample count",
        "Number of requests measured for that cell (here 30 per tier). Percentiles "
        "are only as reliable as n allows — see the note below.",
    ),
]

_CSS = """
:root{
  --bg:#0f1419; --panel:#1a2027; --panel2:#222b35; --ink:#e6edf3; --muted:#9aa7b4;
  --line:#2d3742; --accent:#4493f8; --good:#2ea043; --goodbg:#13351f;
  --bad:#f85149; --badbg:#3a1614; --warn:#d29922; --chip:#30363d;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.skip-link{position:absolute;left:-999px;top:0;background:var(--accent);color:#fff;
  padding:8px 14px;border-radius:0 0 8px 0;z-index:10}
.skip-link:focus{left:0}
.wrap{max-width:1100px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:28px;margin:0 0 4px}
h2{font-size:21px;margin:40px 0 6px;padding-bottom:6px;border-bottom:2px solid var(--line)}
h3{font-size:16px;margin:26px 0 8px}
a{color:var(--accent)}
.sub{color:var(--muted);font-size:13px}
.meta{display:flex;flex-wrap:wrap;gap:8px 20px;margin:14px 0 8px;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 18px}
.meta b{color:var(--ink)} .meta span{color:var(--muted)}
.chip{display:inline-block;background:var(--chip);border-radius:20px;padding:2px 10px;
  font-size:12px;color:var(--ink);margin-right:4px}
.chip.t{background:#1f6feb33;color:#9cc4ff}
.tagrow{margin:2px 0 10px}
.legend{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:6px 4px;margin:18px 0 8px}
.legend details{padding:6px 16px}
.legend summary{cursor:pointer;font-weight:600;font-size:16px;color:var(--ink);padding:8px 0}
.gl{display:grid;grid-template-columns:200px 1fr;gap:6px 18px;padding:8px 0 14px}
.gl dt{font-weight:600;color:var(--accent)}
.gl dt small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.gl dd{margin:0;color:var(--muted)}
.note{background:var(--badbg);border:1px solid #5a2420;border-radius:10px;
  padding:12px 16px;margin:14px 0;color:#ffd3cf;font-size:13px}
.summary-tbl{width:100%;border-collapse:collapse;margin:10px 0 6px;font-size:13px}
.summary-tbl th,.summary-tbl td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--line)}
.summary-tbl th:first-child,.summary-tbl td:first-child{text-align:left}
.summary-tbl thead th{color:var(--muted);font-weight:600;border-bottom:2px solid var(--line);
  position:sticky;top:0;background:var(--bg)}
.summary-tbl tbody tr:hover{background:var(--panel)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin:14px 0}
.card .hdr{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}
.card .mid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--muted)}
.prov{font-size:12px;color:var(--muted);margin:6px 0 10px}
table.cmp{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
table.cmp th,table.cmp td{padding:6px 8px;text-align:right;border-bottom:1px solid var(--line)}
table.cmp th:first-child,table.cmp td:first-child{text-align:left}
table.cmp thead th{color:var(--muted);font-weight:600}
table.cmp .grp{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.metric-name{font-weight:600}
.d-good{color:var(--good);font-weight:600}
.d-bad{color:var(--bad);font-weight:600}
.d-flat{color:var(--muted)}
.sep{border-left:1px solid var(--line)}
.foot{margin-top:50px;color:var(--muted);font-size:12px;text-align:center}
.bar{display:inline-block;height:8px;border-radius:4px;background:var(--accent);vertical-align:middle}
.badge{font-size:11px;padding:1px 7px;border-radius:6px;background:var(--goodbg);color:#7ee787;border:1px solid #2ea04355}
.badge.warn{background:#3a2d10;color:#f0c674;border-color:#d2992255}
"""


def _ms(v, nd=0):
    if v is None:
        return "—"
    return f"{v * 1000:.{nd}f}"


def _delta_cell(default, flex):
    """Return (html, css_class) for a flex-vs-default percent delta."""
    if default is None or flex is None or default == 0:
        return "—", "d-flat"
    pct = (flex - default) / default * 100.0
    if pct <= -3:
        cls = "d-good"
    elif pct >= 3:
        cls = "d-bad"
    else:
        cls = "d-flat"
    return f"{pct:+.0f}%", cls


def _esc(s):
    return html.escape(str(s))


def _glossary_html() -> str:
    rows = []
    for term, short, long in GLOSSARY:
        rows.append(f"<dt>{_esc(term)}<small>{_esc(short)}</small></dt><dd>{_esc(long)}</dd>")
    return (
        '<div class="legend"><details open><summary>📖 Glossary — what every '
        "term means</summary>"
        f'<dl class="gl">{"".join(rows)}</dl></details></div>'
    )


def _pair_cells(cells: list[dict]):
    """Index by (family, model_key, transport) -> {tier: cell}, preserving order."""
    paired: dict[tuple, dict] = {}
    order: list[tuple] = []
    for c in cells:
        key = (c["family"], c["model_key"], c["transport"])
        if key not in paired:
            paired[key] = {}
            order.append(key)
        paired[key][c["tier"]] = c
    return paired, order


def _metric_rows(by_tier: dict[str, dict | None], metric: str) -> str:
    """Build one table row: default p20/p50/p90, then for each compare-tier its
    p20/p50/p90 and Δp50 vs default. Missing tiers render as ``NA``."""
    d = (by_tier.get("default") or {}).get(metric) or {}
    tds = [f'<th scope="row" class="metric-name">{"TTFT" if metric == "ttft" else "Total"}</th>']
    for p in _PCTS:
        tds.append(f"<td>{_ms(d.get(p))}</td>")
    for tier in _COMPARE_TIERS:
        t = (by_tier.get(tier) or {}).get(metric) or {}
        for i, p in enumerate(_PCTS):
            cls = ' class="sep"' if i == 0 else ""
            tds.append(f"<td{cls}>{_ms(t.get(p)) if t else 'NA'}</td>")
        txt, cls = _delta_cell(d.get("p50"), t.get("p50"))
        tds.append(f'<td class="{cls}">{txt}</td>')
    return "<tr>" + "".join(tds) + "</tr>"


def _prov(cell: dict | None, label: str) -> str:
    if not cell:
        return f"<b>{label}:</b> not run"
    served = ", ".join(f"{k}:{v}" for k, v in cell.get("served_tiers", {}).items()) or "—"
    s = f"<b>{label}:</b> {cell['succeeded']}/{cell['requested']} ok · served[{_esc(served)}]"
    if cell.get("failed"):
        errs = ", ".join(f"{k}×{v}" for k, v in cell.get("errors", {}).items())
        s += f' · <span style="color:var(--bad)">{cell["failed"]} fail ({_esc(errs)})</span>'
    return s


def _overview_table(paired, order) -> str:
    """One compact row per (model, transport): TTFT p50 default + flex/priority Δp50."""
    head = (
        '<tr><th scope="col">Model</th><th scope="col">Transport</th>'
        '<th scope="col">Default<br>TTFT p50</th>'
        '<th scope="col">Flex p50</th><th scope="col">Δp50</th>'
        '<th scope="col">Priority p50</th><th scope="col">Δp50</th></tr>'
    )
    rows = []
    for key in order:
        fam, mk, transport = key
        tiers = paired[key]
        d = tiers.get("default")
        dt = (d or {}).get("ttft") or {}
        cells = [
            f'<tr><th scope="row">{_esc((d or next(iter(tiers.values())))["display_name"])}</th>'
            f"<td>{_esc(transport)}</td>",
            f"<td>{_ms(dt.get('p50'))}</td>",
        ]
        for tier in _COMPARE_TIERS:
            t = (tiers.get(tier) or {}).get("ttft") or {}
            if not tiers.get(tier):
                cells.append('<td>NA</td><td class="d-flat">NA</td>')
                continue
            txt, cls = _delta_cell(dt.get("p50"), t.get("p50"))
            cells.append(f'<td>{_ms(t.get("p50"))}</td><td class="{cls}">{txt}</td>')
        cells.append("</tr>")
        rows.append("".join(cells))
    return f'<table class="summary-tbl"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def render(summary: dict) -> str:
    cfg = summary["config"]
    meta = summary["meta"]
    cells = summary["cells"]
    paired, order = _pair_cells(cells)

    total_req = sum(c["succeeded"] for c in cells)
    total_fail = sum(c["failed"] for c in cells)

    meta_html = f"""
    <div class="meta">
      <span>Run</span><b>{_esc(summary["run_id"])}</b>
      <span>Account</span><b>{_esc(meta.get("account_id", "?"))}</b>
      <span>Profile</span><b>{_esc(cfg["profile"])}</b>
      <span>Regions</span><b>{_esc(", ".join(cfg["regions"]))}</b>
      <span>Samples/tier (n)</span><b>{cfg["n_requests"]}</b>
      <span>Interval</span><b>{cfg["interval_seconds"]:.0f}s</b>
      <span>max_tokens</span><b>{cfg["max_tokens"]}</b>
      <span>Requests OK</span><b>{total_req}{" · " + str(total_fail) + " failed" if total_fail else " · 0 failed"}</b>
      <span>Started</span><b>{_esc(meta.get("started", "?")[:19])}Z</b>
      <span>Finished</span><b>{_esc(meta.get("finished", "?")[:19])}Z</b>
    </div>"""

    # per-family detailed cards
    body = []
    last_family = None
    for key in order:
        fam, mk, transport = key
        if fam != last_family:
            body.append(f"<h2>{_esc(fam)}</h2>")
            last_family = fam
        tiers = paired[key]
        d = tiers.get("default")
        any_c = d or next(iter(tiers.values()))
        body.append('<div class="card">')
        body.append(
            f'<div class="hdr"><h3 style="margin:0">{_esc(any_c["display_name"])} '
            f'<span class="chip t">{_esc(transport)}</span> '
            f'<span class="chip">{_esc(any_c["region"])}</span></h3>'
            f'<span class="mid">{_esc(any_c["model_id"])}</span></div>'
        )
        prov_parts = [_prov(d, "Default")]
        for tier in _COMPARE_TIERS:
            prov_parts.append(_prov(tiers.get(tier), tier.capitalize()))
        body.append(f'<div class="prov">{" &nbsp;•&nbsp; ".join(prov_parts)}</div>')
        body.append(
            '<table class="cmp"><thead>'
            "<tr><td></td>"
            '<th colspan="3" scope="colgroup" class="grp">Default (ms)</th>'
            '<th colspan="4" scope="colgroup" class="grp sep">Flex (ms) + Δ</th>'
            '<th colspan="4" scope="colgroup" class="grp sep">Priority (ms) + Δ</th></tr>'
            '<tr><th scope="col">metric</th>'
            '<th scope="col">p20</th><th scope="col">p50</th><th scope="col">p90</th>'
            '<th scope="col" class="sep">p20</th><th scope="col">p50</th>'
            '<th scope="col">p90</th><th scope="col">Δp50</th>'
            '<th scope="col" class="sep">p20</th><th scope="col">p50</th>'
            '<th scope="col">p90</th><th scope="col">Δp50</th>'
            "</tr></thead><tbody>"
        )
        body.append(_metric_rows(tiers, "ttft"))
        body.append(_metric_rows(tiers, "total_latency"))
        body.append("</tbody></table></div>")

    note = (
        '<div class="note">⚠️ <b>On sample size:</b> with n='
        f"{cfg['n_requests']} per tier, the p50 (median) and p90 are reliable; "
        "treat tail latency as directional and re-run with a larger n for firm "
        "tail claims.</div>"
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bedrock Service-Tier Latency Report</title>
<style>{_CSS}</style></head>
<body>
  <a class="skip-link" href="#at-a-glance">Skip to results</a>
  <div class="wrap">
  <h1>Bedrock Service-Tier Latency Benchmark</h1>
  <div class="sub">Time-To-First-Token &amp; total latency — <b>flex</b> and <b>priority</b> tiers, each compared against the standard (<b>default</b>) tier.</div>
  {meta_html}
  <nav aria-label="Report sections">
    {_glossary_html()}
  </nav>
  {note}
  <main>
  <h2 id="at-a-glance">At a glance</h2>
  <div class="sub">One row per model + transport. Green = faster than default, red = slower. Times in milliseconds. NA = tier not served.</div>
  {_overview_table(paired, order)}
  <h2 id="detailed-comparison" style="border:none;margin-top:36px">Detailed comparison</h2>
  <div class="sub">Per cell: default p20/p50/p90, then flex and priority p20/p50/p90 each with Δp50 vs default.</div>
  {"".join(body)}
  </main>
  <div class="foot">Generated by bedrock_bench v{_esc(meta.get("version", "?"))} ·
    measurement via AWS Labs llmeter · {_esc(summary["run_id"])}</div>
</div></body></html>"""


def write_html(summary_json_path: Path, out_path: Path | None = None) -> Path:
    """Read a ``summary.json`` and write the HTML report next to it.

    Args:
        summary_json_path: Path to a run's ``summary.json``.
        out_path: Destination; defaults to ``report.html`` beside the summary.

    Returns:
        The path written.
    """
    summary_json_path = Path(summary_json_path)
    summary = json.loads(summary_json_path.read_text())
    out_path = Path(out_path) if out_path else summary_json_path.parent / "report.html"
    # Atomic publish: temp file + replace, so a crash never leaves partial HTML.
    tmp = out_path.with_name(f".{out_path.name}.tmp")
    tmp.write_text(render(summary))
    os.replace(tmp, out_path)
    return out_path


if __name__ == "__main__":
    import sys

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if src is None:
        raise SystemExit("usage: python -m bedrock_bench.html_report <summary.json> [out.html]")
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    print("wrote", write_html(src, dest))
