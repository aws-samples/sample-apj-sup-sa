"""Cost-Optimized Router — Streamlit Chat Application.

Demonstrates intelligent routing with:
1. Semantic query classification
2. Cost-optimized model selection
3. Production-grade resilience (fallbacks, retries, rate limiting)
"""

import asyncio
import time
from typing import Any

import streamlit as st

from router import SemanticRouter
from model_config import build_model_configs, get_model_configs, MODEL_BY_ID

# Initialize MODEL_CONFIGS at module level before any usage
if not get_model_configs():
    build_model_configs('us-east-1')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_TITLE = "Semantic Router: Cost-Optimized AI"
PAGE_ICON = "🔀"

TIER_COLORS = {1: "#22c55e", 2: "#3b82f6", 3: "#f59e0b", 4: "#ef4444"}
TIER_LABELS = {1: "T1 · Budget", 2: "T2 · Standard", 3: "T3 · Advanced", 4: "T4 · Expert"}

BASELINE_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def _baseline_cost(input_tokens: int, output_tokens: int) -> float:
    """Hypothetical cost if baseline model was used."""
    model = MODEL_BY_ID.get(st.session_state.baseline_model)
    if not model:
        return 0.0
    return (
        model.input_price * input_tokens / 1_000_000 +
        model.output_price * output_tokens / 1_000_000
    )


def _tier_badge(tier: int) -> str:
    """HTML badge for tier."""
    color = TIER_COLORS.get(tier, "#6b7280")
    label = TIER_LABELS.get(tier, f"T{tier}")
    return (
        f'<span style="background:{color};color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-size:0.8em;font-weight:600;">{label}</span>'
    )


# ---------------------------------------------------------------------------
# Session State
# ---------------------------------------------------------------------------


def _init_state() -> None:
    """Initialize session state."""
    all_families = sorted({m.family for m in get_model_configs()})

    defaults: dict[str, Any] = {
        "messages": [],
        "history": [],
        "total_cost": 0.0,
        "baseline_total": 0.0,
        "baseline_model": BASELINE_MODEL_ID,
        "enabled_families": set(all_families),
        "router": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _get_router() -> SemanticRouter:
    """Get or create router instance."""
    if st.session_state.router is None:
        st.session_state.router = SemanticRouter(
            region='us-east-1',
            enabled_families=st.session_state.enabled_families,
        )
    return st.session_state.router


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def _render_sidebar() -> None:
    """Render sidebar with dashboard and config."""
    with st.sidebar:
        # --- Router Status ---
        st.header("🔀 Router Status")
        st.success(f"✅ Semantic Router: **AWS Bedrock**")
        st.divider()

        # --- Cost Dashboard ---
        st.header("📊 Cost Dashboard")

        total_cost = st.session_state.total_cost
        baseline_total = st.session_state.baseline_total
        num_requests = len(st.session_state.history)

        savings_pct = (
            ((baseline_total - total_cost) / baseline_total * 100)
            if baseline_total > 0
            else 0.0
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Cost", f"${total_cost:,.5f}")
        c2.metric("Savings", f"{savings_pct:.1f}%")
        c3.metric("Requests", str(num_requests))

        if num_requests > 0:
            st.subheader("Model Distribution")
            model_counts = {}
            for entry in st.session_state.history:
                model_name = entry.get("model_name", "Unknown")
                model_counts[model_name] = model_counts.get(model_name, 0) + 1
            st.bar_chart(model_counts)

            st.subheader("Tier Distribution")
            tier_counts = {TIER_LABELS[t]: 0 for t in (1, 2, 3, 4)}
            for entry in st.session_state.history:
                tier = entry.get("tier", 0)
                label = TIER_LABELS.get(tier, f"T{tier}")
                tier_counts[label] = tier_counts.get(label, 0) + 1
            st.bar_chart(tier_counts)

        st.divider()

        # --- Configuration ---
        st.header("⚙️ Configuration")

        # Baseline Model
        baseline_options = {
            f"{m.family} Tier {m.tier}": m.model_id
            for m in get_model_configs()
        }
        current_baseline = next(
            (k for k, v in baseline_options.items() if v == st.session_state.baseline_model),
            list(baseline_options.keys())[0],
        )
        selected_baseline = st.selectbox(
            "Baseline Model (for savings calc)",
            options=list(baseline_options.keys()),
            index=list(baseline_options.keys()).index(current_baseline),
        )
        st.session_state.baseline_model = baseline_options[selected_baseline]

        # Model Families
        st.subheader("Enabled Model Families")
        all_families = sorted({m.family for m in get_model_configs()})
        new_families = set()
        for family in all_families:
            if st.toggle(
                family,
                value=family in st.session_state.enabled_families,
                key=f"fam_{family}",
            ):
                new_families.add(family)

        if new_families != st.session_state.enabled_families:
            st.session_state.enabled_families = new_families
            st.session_state.router = None


# ---------------------------------------------------------------------------
# Chat Interface
# ---------------------------------------------------------------------------


def _render_chat() -> None:
    """Render main chat interface."""
    st.title(f"{PAGE_ICON} {PAGE_TITLE}")
    st.caption("Semantic query analysis with cost-optimized model selection")

    # Render conversation history
    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

            # Show routing metadata for assistant messages
            if msg["role"] == "assistant" and i // 2 < len(st.session_state.history):
                entry = st.session_state.history[i // 2]
                _render_routing_info(entry)

    # Chat input
    if prompt := st.chat_input("Ask anything…"):
        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Get response
        with st.chat_message("assistant"):
            with st.spinner("Classifying & routing…"):
                router = _get_router()
                t0 = time.perf_counter()
                result = asyncio.run(
                    router.route_and_respond(
                        prompt,
                        conversation_history=st.session_state.messages[:-1],
                    )
                )
                latency = time.perf_counter() - t0

            # Display response
            st.markdown(result.response)

            # Calculate costs
            cost = result.cost_usd
            bl_cost = _baseline_cost(result.input_tokens, result.output_tokens)

            # Update tracking
            st.session_state.total_cost += cost
            st.session_state.baseline_total += bl_cost

            # Store history
            entry = {
                "model_id": result.model_used,
                "model_name": f"{result.family} T{result.tier}",
                "family": result.family,
                "tier": result.tier,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost": cost,
                "baseline_cost": bl_cost,
                "latency": latency,
                "routing_explanation": result.routing_explanation,
                "classification": {
                    "complexity": result.classification_signals.complexity_score,
                    "task_type": result.classification_signals.task_type,
                    "language": result.classification_signals.language,
                },
            }
            st.session_state.history.append(entry)
            st.session_state.messages.append(
                {"role": "assistant", "content": result.response}
            )

            _render_routing_info(entry)

        st.rerun()


def _render_routing_info(entry: dict[str, Any]) -> None:
    """Display routing metadata below assistant message."""
    model_name = entry.get("model_name", "Unknown")
    tier = entry.get("tier", 0)
    cost = entry.get("cost", 0.0)
    latency = entry.get("latency", 0.0)
    explanation = entry.get("routing_explanation", "")
    classification = entry.get("classification", {})

    badge = _tier_badge(tier)

    st.markdown("---")
    st.markdown(
        f"**Model:** {model_name} &nbsp;{badge}<br>"
        f"**Cost:** ${cost:.6f} &nbsp;| **Latency:** {latency:.2f}s &nbsp;| "
        f"**Tokens:** {entry.get('input_tokens', 0):,} in / {entry.get('output_tokens', 0):,} out",
        unsafe_allow_html=True,
    )

    with st.expander("🔍 Routing Details"):
        st.markdown(f"**Explanation:** {explanation}")
        if classification:
            st.markdown(
                f"**Classification:** Complexity={classification.get('complexity', 0):.2f}, "
                f"Task={classification.get('task_type', 'N/A')}, "
                f"Language={classification.get('language', 'N/A')}"
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Main app entrypoint."""
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=PAGE_ICON,
        layout="wide",
    )
    _init_state()
    _render_sidebar()
    _render_chat()


if __name__ == "__main__":
    main()
