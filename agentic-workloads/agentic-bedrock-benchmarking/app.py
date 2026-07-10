"""Streamlit UI: pick a region, pick models, send a prompt, compare responses (with optional RAG)."""
from __future__ import annotations

import html
import statistics
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

import auth
import judge
import pricing
import quota
import rag
from config import CONFIG, model_allowed
from bedrock import (
    BEDROCK_REGIONS,
    DOCUMENT_FORMATS,
    IMAGE_FORMATS,
    Attachment,
    InvokeResult,
    ModelEntry,
    get_caller_identity,
    invoke,
    list_models,
)

st.set_page_config(page_title="Bedrock Model Benchmarking", layout="wide")

CARD_HEIGHT_PX = 480  # max height of each result card; scrolls internally beyond this

# Engineering-dashboard theming. Tight spacing, monospace metrics, sharp borders.
st.markdown(
    """
    <style>
      :root {
        --fg: #d4d8dd;
        --fg-dim: #8b95a1;
        --fg-mute: #5a6573;
        --accent: #7dd3fc;
        --accent-dim: #38bdf8;
        --bg: #0b0f14;
        --panel: #11161d;
        --panel-2: #161c25;
        --border: #1f2933;
        --ok: #34d399;
        --warn: #fbbf24;
        --err: #f87171;
        --mono: ui-monospace, "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
      }

      .block-container { padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1400px; }

      h1, h2, h3, h4 { letter-spacing: 0.02em; font-weight: 600; }
      h1 { font-size: 1.35rem !important; margin: 0 0 0.25rem 0 !important; text-transform: uppercase; letter-spacing: 0.18em; color: var(--fg); }

      .stApp { background: var(--bg); }
      section[data-testid="stSidebar"] { background: var(--panel); border-right: 1px solid var(--border); }
      section[data-testid="stSidebar"] .block-container { padding-top: 1rem; }

      /* Status bar across the top */
      .statusbar { font-family: var(--mono); font-size: 0.78rem; color: var(--fg-dim);
                   border: 1px solid var(--border); background: var(--panel);
                   padding: 0.45rem 0.75rem; margin: 0.25rem 0 1rem 0; display: flex; gap: 1.5rem; flex-wrap: wrap; }
      .statusbar b { color: var(--fg); font-weight: 600; }
      .statusbar .ok { color: var(--ok); }
      .statusbar .off { color: var(--fg-mute); }

      /* Section labels */
      .section-label { font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.18em;
                       color: var(--fg-mute); text-transform: uppercase; margin: 1rem 0 0.4rem 0; }

      /* Inputs */
      textarea, input, .stTextInput input, .stTextArea textarea, .stNumberInput input {
        font-family: var(--mono) !important;
        background: var(--panel-2) !important;
        color: var(--fg) !important;
        border: 1px solid var(--border) !important;
        border-radius: 2px !important;
      }
      .stTextArea textarea { font-size: 0.88rem !important; line-height: 1.5 !important; }

      /* Buttons */
      .stButton > button {
        font-family: var(--mono) !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        border-radius: 2px !important;
        border: 1px solid var(--border) !important;
        background: var(--panel-2) !important;
        color: var(--fg) !important;
        padding: 0.4rem 0.9rem !important;
      }
      .stButton > button:hover { border-color: var(--accent) !important; color: var(--accent) !important; }
      .stButton > button[kind="primary"] {
        background: var(--accent) !important;
        color: #0b0f14 !important;
        border-color: var(--accent) !important;
        font-weight: 700 !important;
      }
      .stButton > button[kind="primary"]:hover { background: var(--accent-dim) !important; }
      .stButton > button:disabled { opacity: 0.4 !important; }

      /* Selectbox / multiselect */
      div[data-baseweb="select"] > div { background: var(--panel-2) !important; border: 1px solid var(--border) !important; border-radius: 2px !important; font-family: var(--mono) !important; font-size: 0.85rem !important; }

      /* Metric cards */
      div[data-testid="stMetric"] { background: transparent; padding: 0; }
      div[data-testid="stMetricLabel"] { font-family: var(--mono); font-size: 0.65rem !important;
                                          letter-spacing: 0.16em; text-transform: uppercase; color: var(--fg-mute) !important; }
      div[data-testid="stMetricValue"] { font-family: var(--mono); font-size: 1.05rem !important; color: var(--fg) !important; }

      /* Result panel */
      div[data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--panel) !important;
        border: 1px solid var(--border) !important;
        border-radius: 2px !important;
      }
      div[data-testid="stVerticalBlockBorderWrapper"]:hover { border-color: var(--fg-mute) !important; }

      /* Captions */
      .stCaption, [data-testid="stCaptionContainer"] { color: var(--fg-dim) !important; font-family: var(--mono); font-size: 0.75rem !important; }

      /* Code blocks */
      code, pre { font-family: var(--mono) !important; font-size: 0.8rem !important; background: var(--panel-2) !important; color: var(--fg) !important; }

      /* Expander */
      details summary { font-family: var(--mono) !important; font-size: 0.78rem !important; color: var(--fg-dim) !important; letter-spacing: 0.06em; }

      /* Tabs */
      button[data-baseweb="tab"] { font-family: var(--mono) !important; font-size: 0.78rem !important; letter-spacing: 0.1em; text-transform: uppercase; }

      /* Toggle / slider */
      .stSlider { font-family: var(--mono); }

      /* Result card model name */
      .model-name { font-family: var(--mono); font-size: 0.95rem; font-weight: 600; color: var(--fg); margin-bottom: 0.15rem; word-break: break-word; }
      .model-meta { font-family: var(--mono); font-size: 0.7rem; color: var(--fg-mute); margin-bottom: 0.6rem; word-break: break-all; }

      /* Compact stat lines on result cards (replaces the metric grid) */
      .stat-line { font-family: var(--mono); font-size: 0.82rem; color: var(--fg);
                   line-height: 1.7; letter-spacing: 0.01em; margin: 0; }
      .stat-line.stat-dim { color: var(--fg-mute); font-size: 0.74rem; }
      .stat-line .stat-key { color: var(--fg-mute); margin-right: 0.35rem; text-transform: uppercase; font-size: 0.68rem; letter-spacing: 0.1em; }
      .stat-line .stat-sep { color: var(--fg-mute); margin: 0 0.55rem; }
      .stat-line .stat-prefix { color: var(--accent); margin-right: 0.55rem; text-transform: uppercase; font-size: 0.68rem; letter-spacing: 0.1em; }
      .stat-line .stat-warn { color: var(--warn); }
      .stat-line .stat-ok { color: var(--ok); }
      .stat-line .stat-err { color: var(--err); }

      /* Badges (truncated, etc.) */
      .badge { font-family: var(--mono); font-size: 0.65rem; letter-spacing: 0.12em; text-transform: uppercase;
               padding: 2px 6px; border-radius: 2px; border: 1px solid; display: inline-block; margin: 0.4rem 0; }
      .badge-warn { color: var(--warn); border-color: var(--warn); }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=300, show_spinner="loading models...")
def cached_list_models(region: str) -> list[ModelEntry]:
    return list_models(region)


@st.cache_data(ttl=600)
def _validate_credentials() -> None:
    get_caller_identity()


if "rag_index" not in st.session_state:
    st.session_state.rag_index = rag.load_index()
if "last_run" not in st.session_state:
    st.session_state.last_run = None
if "judge_result" not in st.session_state:
    st.session_state.judge_result = None

try:
    _validate_credentials()
except (NoCredentialsError, ClientError, BotoCoreError) as e:
    st.error(f"AWS credentials not available: {e}")
    st.stop()

# Auth gate — no-op in development, blocks until login in production
authed_user = auth.require_login()


def _short_label(m: ModelEntry) -> str:
    name = m.display_name
    if m.kind == "profile":
        return name
    if " / " in name:
        provider, model = name.split(" / ", 1)
        return f"{model}  ·  {provider.lower()}"
    return name


# ---- Sidebar ------------------------------------------------------------

with st.sidebar:
    st.markdown('<div class="section-label">setup</div>', unsafe_allow_html=True)
    if CONFIG.is_production and CONFIG.locked_region:
        region = CONFIG.locked_region
        st.caption(f"region · **{region}** _(locked)_")
    else:
        region = st.selectbox(
            "region",
            BEDROCK_REGIONS,
            index=BEDROCK_REGIONS.index("us-west-2"),
            label_visibility="collapsed",
        )
        st.caption(f"region · {region}")

    try:
        models = cached_list_models(region)
    except (ClientError, BotoCoreError) as e:
        st.error(f"failed to list models: {e}")
        st.stop()

    if not models:
        st.warning(f"no on-demand text models in {region}.")
        st.stop()

    # ---- Attachments (computed first so we can filter the model list) -----
    # The UI is rendered later (under "attachments" section); these widgets read
    # from prior session state via their `key` so values persist across reruns.
    image_uploads_state = st.session_state.get("image_uploader") or []
    doc_uploads_state = st.session_state.get("doc_uploader") or []

    image_attachments: list[Attachment] = []
    for up in image_uploads_state:
        ext = up.name.rsplit(".", 1)[-1].lower() if "." in up.name else "png"
        image_attachments.append(Attachment(name=up.name, fmt=ext, data=up.getvalue(), kind="image"))
    doc_attachments: list[Attachment] = []
    for up in doc_uploads_state:
        ext = up.name.rsplit(".", 1)[-1].lower() if "." in up.name else "txt"
        doc_attachments.append(Attachment(name=up.name, fmt=ext, data=up.getvalue(), kind="document"))
    attachments = image_attachments + doc_attachments
    has_image = bool(image_attachments)
    has_doc = bool(doc_attachments)

    # Filter the model list by production allowlist only.
    # Attachment compatibility is handled per-model at invocation time by _attachments_for().
    capable_models = [m for m in models if model_allowed(m.id)]
    filtered_count = len(models) - len(capable_models)

    options = {_short_label(m): m for m in capable_models}
    st.markdown('<div class="section-label">models</div>', unsafe_allow_html=True)
    selected_names = st.multiselect(
        "models",
        options=list(options.keys()),
        placeholder="type to search...",
        label_visibility="collapsed",
    )
    selected: list[ModelEntry] = [options[n] for n in selected_names]
    if filtered_count:
        st.caption(f"{len(selected)} of {len(capable_models)} selected · {filtered_count} hidden (not in allowlist)")
    else:
        st.caption(f"{len(selected)} of {len(capable_models)} selected")

    if st.button("refresh model list", use_container_width=True):
        cached_list_models.clear()
        st.rerun()

    st.markdown('<div class="section-label">knowledge base</div>', unsafe_allow_html=True)
    rag_idx = st.session_state.rag_index
    if rag_idx is None:
        st.caption("index · empty — add a document below to enable rag")
    else:
        st.caption(f"index · {rag_idx.doc_count} doc / {rag_idx.size} chunk")

    use_rag = st.toggle(
        "use rag",
        value=False,
        disabled=rag_idx is None,
        help="add at least one document before enabling" if rag_idx is None else None,
    )
    top_k = st.slider("top-k", 1, 10, 4, disabled=not use_rag or rag_idx is None)

    with st.expander("manage index", expanded=rag_idx is None):
        if rag_idx is not None:
            st.caption("documents")
            doc_ids = sorted({c.doc_id for c in rag_idx.chunks})
            for did in doc_ids:
                cnt = sum(1 for c in rag_idx.chunks if c.doc_id == did)
                col_a, col_b = st.columns([5, 1])
                col_a.caption(f"`{did}` · {cnt}")
                if col_b.button("rm", key=f"rm-{did}"):
                    new_idx = rag.remove_document(rag_idx, did)
                    if new_idx is None:
                        rag.clear_index()
                    else:
                        rag.save_index(new_idx)
                    st.session_state.rag_index = new_idx
                    st.rerun()

        add_paste, add_file = st.tabs(["paste", "upload"])
        with add_paste:
            paste_id = st.text_input("label", value="pasted", key="paste_label")
            pasted = st.text_area("text", height=120, placeholder="paste text...", key="paste_text", label_visibility="collapsed")
            if st.button("index text", disabled=not pasted.strip(), use_container_width=True):
                try:
                    with st.spinner("embedding..."):
                        new_idx = rag.add_document(
                            st.session_state.rag_index, paste_id.strip() or "pasted", pasted
                        )
                    rag.save_index(new_idx)
                    st.session_state.rag_index = new_idx
                    st.rerun()
                except (ClientError, BotoCoreError, ValueError) as e:
                    st.error(f"indexing failed: {e}")

        with add_file:
            uploads = st.file_uploader(
                "drop .txt / .md / .pdf",
                type=["txt", "md", "pdf"],
                accept_multiple_files=True,
                label_visibility="collapsed",
            )
            if uploads and st.button(f"index {len(uploads)} file(s)", use_container_width=True):
                cur = st.session_state.rag_index
                errors: list[str] = []
                with st.spinner("embedding..."):
                    for up in uploads:
                        data = up.getvalue()
                        try:
                            text = (
                                rag.extract_pdf(data)
                                if up.name.lower().endswith(".pdf")
                                else data.decode("utf-8", errors="replace")
                            )
                            cur = rag.add_document(cur, rag.doc_id_from_upload(up.name, data), text)
                        except (ClientError, BotoCoreError, ValueError) as e:
                            errors.append(f"{up.name}: {e}")
                if cur is not None:
                    rag.save_index(cur)
                    st.session_state.rag_index = cur
                for msg in errors:
                    st.error(msg)
                if not errors:
                    st.rerun()

        if rag_idx is not None and st.button("clear index", use_container_width=True):
            rag.clear_index()
            st.session_state.rag_index = None
            st.rerun()

    st.markdown('<div class="section-label">judge</div>', unsafe_allow_html=True)
    judge_options = {_short_label(m): m for m in models}
    judge_label = st.selectbox(
        "judge model",
        options=["(none)"] + list(judge_options.keys()),
        index=0,
        label_visibility="collapsed",
        help="model used to rank responses after a run",
    )
    judge_model: ModelEntry | None = judge_options.get(judge_label) if judge_label != "(none)" else None
    if judge_model:
        st.caption(f"judge · {judge_model.id}")
    else:
        st.caption("judge · disabled")

# Quota usage (production only)
if CONFIG.is_production and CONFIG.daily_invocation_limit > 0:
    used, limit = quota.current_usage(authed_user.sub)
    pct_color = "ok" if used < limit * 0.7 else ("warn" if used < limit else "err")
    st.sidebar.markdown(
        f"<div class='stat-line stat-dim'>quota · "
        f"<span class='stat-{pct_color}'>{used}/{limit}</span> calls today</div>",
        unsafe_allow_html=True,
    )

# Sign-out button (production only)
auth.render_logout_button()


# ---- Main pane ----------------------------------------------------------

st.markdown("# Bedrock Model Benchmarking")

rag_active = bool(st.session_state.rag_index is not None and use_rag)
rag_pill = (
    f"<span class='ok'>on · k={top_k}</span>"
    if rag_active
    else "<span class='off'>off</span>"
)
attach_pill = (
    f"<span class='ok'>{len(attachments)}</span>" if attachments else "<span class='off'>none</span>"
)
ref_present = bool(st.session_state.get("reference_response", "").strip())
ref_pill = "<span class='ok'>yes</span>" if ref_present else "<span class='off'>none</span>"
st.markdown(
    f"""<div class="statusbar">
        <span>region · <b>{region}</b></span>
        <span>models · <b>{len(selected)}</b></span>
        <span>rag · {rag_pill}</span>
        <span>attachments · {attach_pill}</span>
        <span>reference · {ref_pill}</span>
    </div>""",
    unsafe_allow_html=True,
)

st.markdown('<div class="section-label">prompt</div>', unsafe_allow_html=True)
prompt = st.text_area(
    "prompt",
    height=180,
    placeholder="// write a haiku about caching.\n// try a structured prompt to test instruction-following.",
    label_visibility="collapsed",
)

reference_response = ""
ref_label = (
    f"reference response · {len(st.session_state.get('reference_response', '').split())} words"
    if st.session_state.get("reference_response", "").strip()
    else "reference response (optional)"
)
with st.expander(ref_label, expanded=False):
    st.caption(
        "paste an existing model's response (e.g. from gemini, gpt) to use as the target quality. "
        "the judge will score how well bedrock candidates match or improve on it."
    )
    reference_response = st.text_area(
        "reference",
        height=150,
        placeholder="paste reference output here...",
        label_visibility="collapsed",
        key="reference_response",
    )

with st.expander("advanced", expanded=False):
    a1, a2, a3 = st.columns(3)
    with a1:
        max_tokens = st.number_input(
            "max_tokens",
            min_value=16,
            max_value=CONFIG.max_tokens_cap,
            value=min(1024, CONFIG.max_tokens_cap),
            step=64,
        )
    with a2:
        temperature = st.slider("temperature", min_value=0.0, max_value=1.0, value=0.7, step=0.05)
    with a3:
        runs_per_model = st.slider(
            "runs per model",
            min_value=1,
            max_value=CONFIG.runs_per_model_cap,
            value=1,
            help="N invocations per model in parallel; latency reported as median + p95. Costs scale ~Nx.",
        )

run_disabled = not (prompt.strip() and selected)
total_calls = len(selected) * runs_per_model
runs_suffix = f" · {runs_per_model} runs ({total_calls} calls)" if runs_per_model > 1 else ""
button_label = (
    f"run · {len(selected)} model{'s' if len(selected) != 1 else ''}"
    + runs_suffix
    + ("  ·  rag" if rag_active else "")
    if selected
    else "run"
)

attach_count = len(attachments)
attach_btn_label = f"attach ({attach_count})" if attach_count else "attach"

run_col, attach_col = st.columns([4, 1])
with run_col:
    run = st.button(button_label, type="primary", disabled=run_disabled, use_container_width=True)
with attach_col:
    with st.popover(attach_btn_label, use_container_width=True):
        st.caption("send images / documents along with this prompt")
        st.file_uploader(
            "images (png/jpeg/gif/webp)",
            type=sorted(f for f in IMAGE_FORMATS if f != "jpg") + ["jpg"],
            accept_multiple_files=True,
            key="image_uploader",
        )
        st.file_uploader(
            "documents (pdf/doc/docx/xls/xlsx/csv/html/txt/md)",
            type=sorted(f for f in DOCUMENT_FORMATS if f != "htm"),
            accept_multiple_files=True,
            key="doc_uploader",
        )
        if attachments:
            bits = []
            if has_image:
                bits.append(f"{len(image_attachments)} image")
            if has_doc:
                bits.append(f"{len(doc_attachments)} doc")
            st.caption("attached · " + ", ".join(bits))

if run_disabled and not run:
    if not selected and not prompt.strip():
        st.caption("> select models in the sidebar and write a prompt.")
    elif not selected:
        st.caption("> select at least one model in the sidebar.")
    elif not prompt.strip():
        st.caption("> write a prompt above.")

def _model_short(m: ModelEntry) -> str:
    return m.display_name.split(" / ", 1)[1] if " / " in m.display_name else m.display_name


def _attachments_for(model: ModelEntry, all_attachments: list[Attachment]) -> list[Attachment]:
    """Strip attachments a model can't handle so we can still show a row instead of a hard error."""
    out: list[Attachment] = []
    for att in all_attachments:
        if att.kind == "image" and not model.supports_image:
            continue
        if att.kind == "document" and not model.supports_document:
            continue
        out.append(att)
    return out


def _cached_pricing(model_id: str) -> tuple[Optional[float], Optional[float], int]:
    """Pricing lookup. pricing.get_pricing has its own in-process cache for known models;
    skipping st.cache_data here so unknown models get retried after a fallback-table update."""
    p = pricing.get_pricing(model_id)
    return p.input_per_1k_usd, p.output_per_1k_usd, p.raw_skus


def _aggregate(runs: list[InvokeResult]) -> dict:
    """Compute median + p95 for latency / ttft / tpot across runs of one model."""
    successful = [r for r in runs if not r.error]
    if not successful:
        return {
            "n_total": len(runs),
            "n_success": 0,
            "errors": [r.error for r in runs if r.error],
            "primary": runs[0] if runs else None,
        }
    latencies = [r.latency_ms for r in successful]
    ttfts = [r.ttft_ms for r in successful if r.ttft_ms is not None]
    tpots = [r.tpot_ms for r in successful if r.tpot_ms is not None]

    def _p(xs: list, q: float):
        if not xs:
            return None
        xs_sorted = sorted(xs)
        idx = max(0, min(len(xs_sorted) - 1, int(round(q * (len(xs_sorted) - 1)))))
        return xs_sorted[idx]

    return {
        "n_total": len(runs),
        "n_success": len(successful),
        "errors": [r.error for r in runs if r.error],
        "primary": successful[0],
        "latency_p50": int(statistics.median(latencies)),
        "latency_p95": int(_p(latencies, 0.95)),
        "ttft_p50": int(statistics.median(ttfts)) if ttfts else None,
        "ttft_p95": int(_p(ttfts, 0.95)) if ttfts else None,
        "tpot_p50": float(statistics.median(tpots)) if tpots else None,
        "tpot_p95": float(_p(tpots, 0.95)) if tpots else None,
    }


def _render_card_finalized(model: ModelEntry, agg: dict, max_tokens_used: int) -> None:
    """Final, non-live card. Stripped to header + response + warning badges; metrics live in the
    comparison table below the grid."""
    with st.container(border=True, height=CARD_HEIGHT_PX):
        provider = model.provider if model.kind == "foundation" else "profile"
        st.markdown(f"<div class='model-name'>{html.escape(_model_short(model))}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='model-meta'>{html.escape(provider.lower())} · {html.escape(model.id)}</div>", unsafe_allow_html=True)

        primary = agg["primary"]
        if primary is None or agg["n_success"] == 0:
            st.error(agg["errors"][0] if agg["errors"] else "all runs failed")
            return

        if primary.truncated:
            st.markdown(
                f"<span class='badge badge-warn' title='hit max_tokens cap; raise max_tokens for full output'>truncated · max_tokens={max_tokens_used}</span>",
                unsafe_allow_html=True,
            )
        elif primary.stop_reason and primary.stop_reason not in ("end_turn", "stop"):
            st.markdown(
                f"<span class='badge badge-warn'>stop · {html.escape(primary.stop_reason)}</span>",
                unsafe_allow_html=True,
            )
        st.markdown(primary.text)


def _render_metrics_table(aggregates: list[tuple[ModelEntry, dict]]) -> None:
    """Single dataframe summarizing all models' performance metrics. Replaces per-card metric stripes."""
    if not aggregates:
        return
    any_multi = any(agg["n_total"] > 1 for _, agg in aggregates)

    rows = []
    for model, agg in aggregates:
        primary = agg["primary"]
        if primary is None or agg["n_success"] == 0:
            row = {
                "model": _model_short(model),
                "ttft p50": "—",
                "tpot p50": "—",
                "total p50": "—",
                "in/out": "—",
                "cost": "—",
                "price (in/out per M)": "—",
            }
            if any_multi:
                row["ttft p95"] = "—"
                row["total p95"] = "—"
                row["n ok/total"] = f"0/{agg['n_total']}"
            rows.append(row)
            continue

        in_p, out_p, _ = _cached_pricing(model.id)
        if in_p is not None and out_p is not None:
            cost = (primary.input_tokens / 1000.0) * in_p + (primary.output_tokens / 1000.0) * out_p
            cost_total = cost * agg["n_success"]
            cost_str = pricing.format_cost(cost_total)
            price_str = (
                f"{pricing.format_per_million(in_p)} / "
                f"{pricing.format_per_million(out_p)}"
            )
        else:
            cost_str = "—"
            price_str = "—"

        ttft_str = f"{agg['ttft_p50']/1000:.2f}s" if agg["ttft_p50"] is not None else "—"
        tpot_str = f"{agg['tpot_p50']:.0f}ms/t" if agg["tpot_p50"] is not None else "—"
        total_str = f"{agg['latency_p50']/1000:.2f}s"

        row = {
            "model": _model_short(model),
            "ttft p50": ttft_str,
            "tpot p50": tpot_str,
            "total p50": total_str,
            "in/out": f"{primary.input_tokens}/{primary.output_tokens}t",
            "cost": cost_str,
            "price (in/out per M)": price_str,
        }
        if any_multi:
            row["ttft p95"] = f"{agg['ttft_p95']/1000:.2f}s" if agg.get("ttft_p95") is not None else "—"
            row["total p95"] = f"{agg['latency_p95']/1000:.2f}s" if agg.get("latency_p95") is not None else "—"
            row["n ok/total"] = f"{agg['n_success']}/{agg['n_total']}"
        rows.append(row)

    st.markdown('<div class="section-label">metrics</div>', unsafe_allow_html=True)
    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "model": st.column_config.TextColumn("model"),
            "ttft p50": st.column_config.TextColumn("ttft" + (" p50" if any_multi else ""), help="time to first token (median across runs)"),
            "tpot p50": st.column_config.TextColumn("tpot" + (" p50" if any_multi else ""), help="time per output token"),
            "total p50": st.column_config.TextColumn("total" + (" p50" if any_multi else ""), help="end-to-end latency"),
            "in/out": st.column_config.TextColumn("in/out", help="input/output token counts"),
            "cost": st.column_config.TextColumn("cost", help="estimated USD across all successful runs"),
            "price (in/out per M)": st.column_config.TextColumn("price /M", help="USD per million input / output tokens"),
            "ttft p95": st.column_config.TextColumn("ttft p95"),
            "total p95": st.column_config.TextColumn("total p95"),
            "n ok/total": st.column_config.TextColumn("n", help="successful / total runs"),
        },
    )


if run:
    # Per-user quota check (no-op in dev). Counts both candidate runs AND the judge call.
    n_calls = len(selected) * runs_per_model + (1 if judge_model else 0)
    qc = quota.check_and_increment(authed_user.sub, n_calls)
    if not qc.allowed:
        st.error(f"daily quota exceeded · {qc.reason}")
        st.stop()

    final_prompt = prompt
    hits: list[rag.Hit] = []
    if rag_active:
        try:
            with st.spinner(f"retrieving top-{top_k}..."):
                hits = rag.retrieve(st.session_state.rag_index, prompt, k=int(top_k))
            final_prompt = rag.build_augmented_prompt(prompt, hits)
        except (ClientError, BotoCoreError) as e:
            st.error(f"retrieval failed: {e}")
            st.stop()

    if rag_active:
        with st.expander(f"retrieved context · {len(hits)} chunks", expanded=False):
            for i, h in enumerate(hits, 1):
                st.markdown(f"`[{i}] {h.chunk.doc_id}` · chunk {h.chunk.chunk_idx} · score `{h.score:.3f}`")
                st.caption(h.chunk.text[:600] + ("..." if len(h.chunk.text) > 600 else ""))
        with st.expander("augmented prompt", expanded=False):
            st.code(final_prompt)

    if reference_response and reference_response.strip():
        with st.expander(f"reference response · {len(reference_response.split())} words", expanded=False):
            st.markdown(reference_response)

    st.markdown('<div class="section-label">results</div>', unsafe_allow_html=True)

    # ---- Pre-create card placeholders for fixed-position live streaming ----
    cols_per_row = 3
    text_placeholders: dict[str, "st.delta_generator.DeltaGenerator"] = {}
    finalize_placeholders: dict[str, "st.delta_generator.DeltaGenerator"] = {}

    for row_start in range(0, len(selected), cols_per_row):
        row = selected[row_start : row_start + cols_per_row]
        cols = st.columns(len(row))
        for col, model in zip(cols, row):
            with col:
                # Outer placeholder for the entire card; we render a "streaming" view, then replace
                # with finalized view once all runs finish.
                slot = st.empty()
                with slot.container(border=True, height=CARD_HEIGHT_PX):
                    provider = model.provider if model.kind == "foundation" else "profile"
                    st.markdown(f"<div class='model-name'>{html.escape(_model_short(model))}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='model-meta'>{html.escape(provider.lower())} · {html.escape(model.id)}</div>", unsafe_allow_html=True)
                    st.caption("streaming...")
                    text_placeholders[model.id] = st.empty()
                finalize_placeholders[model.id] = slot

    # ---- Run N invocations per model in parallel ----
    # Only the FIRST run of each model streams live (writes to text_placeholders[model.id]).
    # The remaining runs just collect timing data.
    streamed_text: dict[str, str] = {m.id: "" for m in selected}

    def _run_one(model: ModelEntry, is_primary: bool) -> tuple[ModelEntry, InvokeResult]:
        atts = _attachments_for(model, attachments)

        def _on_chunk(chunk: str) -> None:
            if not is_primary:
                return
            streamed_text[model.id] += chunk
            try:
                text_placeholders[model.id].markdown(streamed_text[model.id])
            except Exception:
                pass

        res = invoke(
            region=region,
            model_id=model.id,
            prompt=final_prompt,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            attachments=atts,
            on_chunk=_on_chunk if is_primary else None,
        )
        return (model, res)

    jobs: list[tuple[ModelEntry, bool]] = []
    for m in selected:
        for i in range(runs_per_model):
            jobs.append((m, i == 0))

    runs_by_model: dict[str, list[InvokeResult]] = {m.id: [] for m in selected}
    with ThreadPoolExecutor(max_workers=max(1, min(len(jobs), 16))) as pool:
        futures = [pool.submit(_run_one, m, primary) for m, primary in jobs]
        for f in futures:
            model, res = f.result()
            runs_by_model[model.id].append(res)

    # ---- Aggregate and finalize cards ----
    aggregates: list[tuple[ModelEntry, dict]] = []
    for model in selected:
        agg = _aggregate(runs_by_model[model.id])
        aggregates.append((model, agg))

    for model, agg in aggregates:
        slot = finalize_placeholders[model.id]
        slot.empty()
        with slot:
            _render_card_finalized(model, agg, int(max_tokens))

    _render_metrics_table(aggregates)

    st.session_state.last_run = {
        "user_prompt": prompt,
        "final_prompt": final_prompt,
        "selected": selected,
        "aggregates": aggregates,
        "hits": hits,
        "rag_active": rag_active,
        "region": region,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "runs_per_model": runs_per_model,
        "reference_response": reference_response.strip() if reference_response else "",
    }
    st.session_state.judge_result = None
    st.session_state.skip_last_render_once = True


last = st.session_state.last_run
if last and not st.session_state.get("skip_last_render_once"):
    if last["rag_active"]:
        with st.expander(f"retrieved context · {len(last['hits'])} chunks", expanded=False):
            for i, h in enumerate(last["hits"], 1):
                st.markdown(f"`[{i}] {h.chunk.doc_id}` · chunk {h.chunk.chunk_idx} · score `{h.score:.3f}`")
                st.caption(h.chunk.text[:600] + ("..." if len(h.chunk.text) > 600 else ""))
        with st.expander("augmented prompt", expanded=False):
            st.code(last["final_prompt"])

    last_ref = last.get("reference_response") or ""
    if last_ref.strip():
        with st.expander(f"reference response · {len(last_ref.split())} words", expanded=False):
            st.markdown(last_ref)

    st.markdown('<div class="section-label">results</div>', unsafe_allow_html=True)
    aggregates = last["aggregates"]
    cols_per_row = 3
    for row_start in range(0, len(aggregates), cols_per_row):
        row = aggregates[row_start : row_start + cols_per_row]
        cols = st.columns(len(row))
        for col, (model, agg) in zip(cols, row):
            with col:
                _render_card_finalized(model, agg, last["max_tokens"])
    _render_metrics_table(aggregates)
elif last and st.session_state.get("skip_last_render_once"):
    # Don't double-render; the live cards above are already finalized. Reset flag for next pass.
    st.session_state.skip_last_render_once = False

if last:

    # ---- Judge / quality evaluation ----
    st.markdown('<div class="section-label">quality evaluation</div>', unsafe_allow_html=True)

    # Build (model, primary_result) pairs from aggregates, dropping models that had zero successful runs.
    successful: list[tuple[ModelEntry, InvokeResult]] = []
    for model, agg in last["aggregates"]:
        if agg["n_success"] > 0 and agg["primary"] is not None:
            successful.append((model, agg["primary"]))

    if not judge_model:
        st.caption("> select a judge model in the sidebar to enable quality evaluation.")
    elif len(successful) < 2:
        st.caption("> need at least 2 successful responses to evaluate.")
    else:
        eval_clicked = st.button(
            f"evaluate quality  ·  judge: {judge_model.id.split('.')[-1] if '.' in judge_model.id else judge_model.id}",
            type="primary",
            key="eval_btn",
        )
        if eval_clicked:
            labels = [chr(ord("A") + i) for i in range(len(successful))]
            payload = [
                (label, m.id, r.text)
                for label, (m, r) in zip(labels, successful)
            ]
            with st.spinner(f"judge ranking {len(successful)} responses..."):
                jr = judge.evaluate(
                    region=last["region"],
                    judge_model_id=judge_model.id,
                    user_prompt=last["user_prompt"],
                    responses=payload,
                    reference_response=last.get("reference_response") or None,
                )
            st.session_state.judge_result = jr
            st.session_state.judge_label_map = {m.id: lbl for lbl, (m, _) in zip(labels, successful)}

    jr = st.session_state.judge_result
    if jr:
        if jr.error:
            st.error(f"judge error: {jr.error}")
            with st.expander("raw judge response", expanded=False):
                st.code(jr.raw_response or "(no response)")
        elif not jr.scores:
            st.warning("judge returned no scores.")
            with st.expander("raw judge response", expanded=False):
                st.code(jr.raw_response or "(no response)")
        else:
            label_map = st.session_state.get("judge_label_map", {})
            successful_by_id = {m.id: (m, r) for m, r in successful}

            rows = []
            for s in jr.scores:
                model_entry, primary = successful_by_id.get(s.model_id, (None, None))
                model_name = (
                    model_entry.display_name.split(" / ", 1)[1]
                    if model_entry and " / " in model_entry.display_name
                    else (model_entry.display_name if model_entry else s.model_id)
                )
                avg_rank = sum(s.ranks.values()) / max(1, len([v for v in s.ranks.values() if v > 0]))
                avg_score = sum(s.scores.values()) / max(1, len([v for v in s.scores.values() if v > 0]))

                # Cost + cost-per-quality
                cost_str = "—"
                cost_per_quality_str = "—"
                if model_entry and primary:
                    in_p, out_p, _ = _cached_pricing(model_entry.id)
                    if in_p is not None and out_p is not None:
                        cost = (primary.input_tokens / 1000.0) * in_p + (primary.output_tokens / 1000.0) * out_p
                        cost_str = pricing.format_cost(cost)
                        if avg_score > 0:
                            cost_per_quality_str = pricing.format_cost(cost / avg_score)

                row = {
                    "label": label_map.get(s.model_id, "?"),
                    "model": model_name,
                    "correctness": s.scores.get("correctness", 0),
                    "instruction": s.scores.get("instruction_following", 0),
                    "completeness": s.scores.get("completeness", 0),
                    "clarity": s.scores.get("clarity", 0),
                }
                if jr.has_reference:
                    row["match"] = s.scores.get("match_to_reference", 0)
                row.update({
                    "avg score": round(avg_score, 2),
                    "avg rank": round(avg_rank, 2),
                    "cost": cost_str,
                    "$/quality": cost_per_quality_str,
                    "rationale": s.rationale,
                })
                rows.append(row)

            rows.sort(key=lambda r: r["avg rank"])

            winner_caption = ""
            if jr.overall_winner:
                winner_entry = successful_by_id.get(jr.overall_winner)
                if winner_entry:
                    winner_name = (
                        winner_entry[0].display_name.split(" / ", 1)[1]
                        if " / " in winner_entry[0].display_name
                        else winner_entry[0].display_name
                    )
                    winner_caption = f"overall winner · {winner_name}"
            if winner_caption:
                st.caption(winner_caption)
            st.caption(f"judge · {jr.judge_model_id}")

            col_config = {
                "label": st.column_config.TextColumn("#", width="small"),
                "model": st.column_config.TextColumn("model"),
                "correctness": st.column_config.NumberColumn("correctness", format="%.1f", help="1-10 absolute score"),
                "instruction": st.column_config.NumberColumn("instruction", format="%.1f"),
                "completeness": st.column_config.NumberColumn("completeness", format="%.1f"),
                "clarity": st.column_config.NumberColumn("clarity", format="%.1f"),
                "avg score": st.column_config.NumberColumn("avg score", format="%.2f"),
                "avg rank": st.column_config.NumberColumn("avg rank", format="%.2f", help="1 = best (lower is better)"),
                "cost": st.column_config.TextColumn("cost", help="estimated USD for this single response"),
                "$/quality": st.column_config.TextColumn("$/quality", help="cost / avg quality score (lower is better value)"),
                "rationale": st.column_config.TextColumn("rationale", width="large"),
            }
            if jr.has_reference:
                col_config["match"] = st.column_config.NumberColumn(
                    "match", format="%.1f", help="how well this candidate matches the reference response (1-10)"
                )

            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True,
                column_config=col_config,
            )

            with st.expander("raw judge response", expanded=False):
                st.code(jr.raw_response)
