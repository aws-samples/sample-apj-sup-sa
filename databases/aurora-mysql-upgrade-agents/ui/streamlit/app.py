"""MySQL Upgrade Readiness Checker — Streamlit UI (optional).

Runs locally; uses the local AWS credential chain (env vars / ~/.aws) to call
the Bedrock AgentCore Runtime deployed by the CDK stack in ../../infra.
"""
from __future__ import annotations

import codecs
import json
import os
import time
import uuid
from pathlib import Path

import boto3
import requests
import streamlit as st
from botocore.config import Config
from dotenv import load_dotenv

# Load shared config from infra/.env first (VPC / DB / hosts — same values the
# CDK stack was deployed with). Then let ui/.env override or add anything
# UI-specific (mainly the 4 Agent ARNs emitted as CloudFormation Outputs).
_HERE = Path(__file__).resolve().parent
_INFRA_ENV = _HERE.parents[1] / "infra" / ".env"
if _INFRA_ENV.exists():
    load_dotenv(_INFRA_ENV)
load_dotenv(_HERE / ".env", override=True)

# --- Config -----------------------------------------------------------------
# CDK_DEFAULT_REGION is the name the infra/.env uses; AWS_REGION is a
# conventional fallback if a user sets only that.
AWS_REGION = os.getenv("CDK_DEFAULT_REGION") or os.getenv("AWS_REGION", "us-east-1")
ORCHESTRATOR_ARN = os.getenv("ORCHESTRATOR_ARN", "")
ERROR_LOG_ANALYZER_ARN = os.getenv("ERROR_LOG_ANALYZER_ARN", "")
VARIABLES_COMPARE_ARN = os.getenv("VARIABLES_COMPARE_ARN", "")
UPGRADE_READINESS_ARN = os.getenv("UPGRADE_READINESS_ARN", "")
QUERY_RISK_SCORER_ARN = os.getenv("QUERY_RISK_SCORER_ARN", "")
PLAN_DIFF_ARN = os.getenv("PLAN_DIFF_ARN", "")
BLUE_HOST = os.getenv("BLUE_HOST", "")
GREEN_HOST = os.getenv("GREEN_HOST", "")
# DB password lives in Secrets Manager; the agents resolve it at run time.
# Only the secret id (name or ARN) travels in the payload — never the password.
DB_SECRET_ID = os.getenv("DB_SECRET_ID", "")
DB_USER = os.getenv("DB_USER", "")  # optional override of the secret's username
GREEN_LOG_GROUP = os.getenv("GREEN_LOG_GROUP", "")
# Report output language: "ko" (default) or "en".
REPORT_LANGUAGE = (os.getenv("REPORT_LANGUAGE", "ko") or "ko").strip().lower()
if REPORT_LANGUAGE not in ("ko", "en"):
    REPORT_LANGUAGE = "ko"
_REPORTS_BUCKET_BASE = os.getenv("REPORTS_BUCKET_NAME", "aurora-mysql-upgrade-reports")
_DEPLOYMENT_SUFFIX = os.getenv("DEPLOYMENT_SUFFIX", "").strip()
S3_BUCKET = (
    f"{_REPORTS_BUCKET_BASE}-{_DEPLOYMENT_SUFFIX}"
    if _DEPLOYMENT_SUFFIX
    else _REPORTS_BUCKET_BASE
)

REQUIRED_ENV = {
    "ORCHESTRATOR_ARN": ORCHESTRATOR_ARN,
    "ERROR_LOG_ANALYZER_ARN": ERROR_LOG_ANALYZER_ARN,
    "VARIABLES_COMPARE_ARN": VARIABLES_COMPARE_ARN,
    "UPGRADE_READINESS_ARN": UPGRADE_READINESS_ARN,
    "QUERY_RISK_SCORER_ARN": QUERY_RISK_SCORER_ARN,
    "PLAN_DIFF_ARN": PLAN_DIFF_ARN,
    "BLUE_HOST": BLUE_HOST,
    "GREEN_HOST": GREEN_HOST,
    "DB_SECRET_ID": DB_SECRET_ID,
    "GREEN_LOG_GROUP": GREEN_LOG_GROUP,
}


# --- i18n -------------------------------------------------------------------
# All user-facing UI strings, keyed by language. Same REPORT_LANGUAGE that is
# sent to the agents drives the UI language too. {placeholders} are filled by
# t() via str.format.
STRINGS = {
    "ko": {
        "lang_name": "한국어",
        "fetch_failed": "리포트 다운로드 실패 (HTTP {status}): {rtype}",
        "fetch_error": "리포트 다운로드 오류: {exc}",
        "step_done": "✅ Step {step} 완료 ({elapsed}s)",
        "label_status": "상태",
        "label_message": "메시지",
        "label_blue": "Blue",
        "label_green": "Green",
        "label_blue_vars": "Blue 변수",
        "label_green_vars": "Green 변수",
        "label_log_events": "로그 이벤트",
        "label_risky_queries": "리스크 쿼리",
        "report_url_ready": "   📥 리포트 URL 생성됨",
        "workflow_complete": "\n{sep}\n🎉 분석 완료 (총 {seconds}초)\n{sep}",
        "workflow_stopped": "\n⚠️ 분석 중단: {reason}\n   {message}",
        "start_banner": "🚀 분석 시작...",
        "env_ready": "✅ 설정 완료 — 리전 `{region}` / 버킷 `{bucket}` / 리포트 언어 `{lang}`",
        "env_missing": "❌ `.env` 에 누락된 값이 있습니다: `{missing}`",
        "reports_header": "📥 리포트 다운로드",
        "reports_pending": "분석 완료 후 리포트가 여기에 표시됩니다.",
        "reports_empty": "생성된 리포트가 없습니다.",
        "page_caption": "Aurora MySQL 3.04 → 3.10 마이너 업그레이드 준비 상태를 분석합니다.",
        "btn_run": "🚀 분석 시작",
        "btn_clear": "🗑️ 출력 지우기",
        "progress_header": "진행 상황",
        "detail_log": "상세 로그",
        "terminal_idle": "대기 중... '분석 시작' 버튼을 클릭하세요.",
        "run_error": "\n\n❌ 실행 오류: {exc}",
    },
    "en": {
        "lang_name": "English",
        "fetch_failed": "Report download failed (HTTP {status}): {rtype}",
        "fetch_error": "Report download error: {exc}",
        "step_done": "✅ Step {step} complete ({elapsed}s)",
        "label_status": "Status",
        "label_message": "Message",
        "label_blue": "Blue",
        "label_green": "Green",
        "label_blue_vars": "Blue variables",
        "label_green_vars": "Green variables",
        "label_log_events": "Log events",
        "label_risky_queries": "Risky queries",
        "report_url_ready": "   📥 Report URL generated",
        "workflow_complete": "\n{sep}\n🎉 Analysis complete (total {seconds}s)\n{sep}",
        "workflow_stopped": "\n⚠️ Analysis stopped: {reason}\n   {message}",
        "start_banner": "🚀 Starting analysis...",
        "env_ready": "✅ Ready — region `{region}` / bucket `{bucket}` / report language `{lang}`",
        "env_missing": "❌ Missing values in `.env`: `{missing}`",
        "reports_header": "📥 Download Reports",
        "reports_pending": "Reports will appear here after the analysis completes.",
        "reports_empty": "No reports were generated.",
        "page_caption": "Analyzes Aurora MySQL 3.04 → 3.10 minor upgrade readiness.",
        "btn_run": "🚀 Start Analysis",
        "btn_clear": "🗑️ Clear Output",
        "progress_header": "Progress",
        "detail_log": "Detailed Log",
        "terminal_idle": "Idle... Click the 'Start Analysis' button.",
        "run_error": "\n\n❌ Run error: {exc}",
    },
}


def t(key: str, **kwargs) -> str:
    """Look up a UI string for the active language and fill placeholders."""
    table = STRINGS.get(REPORT_LANGUAGE, STRINGS["ko"])
    template = table.get(key) or STRINGS["ko"].get(key, key)
    return template.format(**kwargs) if kwargs else template


# --- Session state ----------------------------------------------------------
def _init_state() -> None:
    defaults = {
        "running": False,
        "final_result": None,
        "report_contents": {},
        "terminal_output": "",
        "steps": {},  # step_num -> {"tool":.., "status":.., "elapsed":..}
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


# --- Boto3 ------------------------------------------------------------------
def agentcore_client():
    config = Config(
        retries={"max_attempts": 1, "mode": "standard"},
        read_timeout=600,
        connect_timeout=60,
    )
    return boto3.client("bedrock-agentcore", region_name=AWS_REGION, config=config)


# --- Report fetch -----------------------------------------------------------
def fetch_report(url: str, report_type: str) -> bytes | None:
    cache = st.session_state.report_contents
    if report_type in cache:
        return cache[report_type]
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            cache[report_type] = r.content
            return r.content
        st.warning(t("fetch_failed", status=r.status_code, rtype=report_type))
    except Exception as exc:  # noqa: BLE001
        st.warning(t("fetch_error", exc=exc))
    return None


# --- Event handling ---------------------------------------------------------
def handle_event(event: dict, terminal_lines: list[str]) -> None:
    et = event.get("event_type", "")
    steps = st.session_state.steps

    if et == "step_start":
        step = event.get("step", "?")
        steps[step] = {
            "tool": event.get("tool", "unknown"),
            "status": "running",
            "elapsed": None,
            "message": event.get("message", ""),
        }
        terminal_lines.append(
            f"\n{'='*50}\n🔧 Step {step}: {steps[step]['tool']}\n"
            f"   {steps[step]['message']}\n{'-'*50}"
        )

    elif et == "progress":
        step = event.get("step", "?")
        msg = event.get("message", "")
        terminal_lines.append(f"   ⏳ [{step}] {msg}")

    elif et == "step_complete":
        step = event.get("step", "?")
        if step in steps:
            steps[step]["status"] = "done"
            steps[step]["elapsed"] = event.get("elapsed_seconds", 0)
        result = event.get("result", {}) or {}
        lines = [t("step_done", step=step, elapsed=event.get("elapsed_seconds", 0))]
        for key, label in [
            ("status", t("label_status")),
            ("message", t("label_message")),
            ("blue_version", t("label_blue")),
            ("green_version", t("label_green")),
            ("blue_variable_count", t("label_blue_vars")),
            ("green_variable_count", t("label_green_vars")),
            ("log_events", t("label_log_events")),
            ("risky_queries_count", t("label_risky_queries")),
        ]:
            if result.get(key) is not None:
                lines.append(f"   {label}: {result[key]}")
        if result.get("presigned_url"):
            lines.append(t("report_url_ready"))
        terminal_lines.append("\n".join(lines))

    elif et == "summary_stream":
        terminal_lines.append(event.get("text", ""))

    elif et == "workflow_complete":
        st.session_state.final_result = event
        for report in event.get("report_urls", []):
            if report.get("url") and report.get("type"):
                fetch_report(report["url"], report["type"])
        terminal_lines.append(
            t("workflow_complete", sep="=" * 50,
              seconds=event.get("total_elapsed_seconds", 0))
        )

    elif et == "workflow_stopped":
        terminal_lines.append(
            t("workflow_stopped", reason=event.get("reason", "?"),
              message=event.get("message", ""))
        )

    elif et == "error":
        terminal_lines.append(f"\n❌ Error: {event.get('message', 'Unknown')}")


# --- Stream consumer --------------------------------------------------------
def run_orchestrator(payload: dict, step_slot, terminal_slot) -> None:
    client = agentcore_client()
    session_id = f"streamlit-orch-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    response = client.invoke_agent_runtime(
        agentRuntimeArn=ORCHESTRATOR_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode(),
    )

    raw = response["response"]._raw_stream
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    byte_buf = b""
    line_buf = ""
    terminal_lines: list[str] = [t("start_banner")]

    while True:
        chunk = raw.read(512)
        if not chunk:
            break
        byte_buf += chunk
        text = decoder.decode(byte_buf, final=False)
        byte_buf = b""
        if not text:
            continue
        text = line_buf + text
        line_buf = ""
        parts = text.split("\n")
        if not text.endswith("\n"):
            line_buf = parts[-1]
            parts = parts[:-1]

        for line in parts:
            line = line.strip()
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            handle_event(event, terminal_lines)
            st.session_state.terminal_output = "\n".join(terminal_lines)
            _render_steps(step_slot)
            terminal_slot.code(st.session_state.terminal_output, language=None)


# --- UI helpers -------------------------------------------------------------
STATUS_ICON = {"running": "⏳", "done": "✅", "failed": "❌"}


def _render_steps(slot) -> None:
    steps = st.session_state.steps
    if not steps:
        slot.empty()
        return
    lines = []
    for step in sorted(steps.keys(), key=lambda x: (isinstance(x, str), x)):
        s = steps[step]
        icon = STATUS_ICON.get(s["status"], "•")
        elapsed = f" ({s['elapsed']}s)" if s["elapsed"] is not None else ""
        lines.append(f"{icon} **Step {step}** — {s['tool']}{elapsed}")
    slot.markdown("\n\n".join(lines))


def _env_badge() -> None:
    missing = [k for k, v in REQUIRED_ENV.items() if not v]
    if not missing:
        st.success(
            t("env_ready", region=AWS_REGION, bucket=S3_BUCKET, lang=t("lang_name"))
        )
    else:
        st.error(t("env_missing", missing=", ".join(missing)))


def _sidebar_reports() -> None:
    st.header(t("reports_header"))
    result = st.session_state.final_result
    if not result:
        st.caption(t("reports_pending"))
        return
    reports = result.get("report_urls", []) or []
    if not reports:
        st.info(t("reports_empty"))
        return
    for i, report in enumerate(reports):
        name = report.get("name", f"Report {i+1}")
        url = report.get("url", "")
        rtype = report.get("type", "unknown")
        if not url:
            continue
        filename = url.split("?")[0].split("/")[-1]
        if not filename.endswith(".md"):
            filename = f"{rtype}_report.md"
        content = st.session_state.report_contents.get(rtype) or fetch_report(url, rtype)
        if content:
            st.download_button(
                f"📥 {name}",
                data=content,
                file_name=filename,
                mime="text/markdown",
                key=f"dl_{rtype}_{i}",
                use_container_width=True,
            )
        else:
            st.markdown(f"[📄 {name}]({url})")


# --- Main -------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="MySQL Upgrade Readiness Checker",
        page_icon="🔍",
        layout="wide",
    )
    _init_state()

    st.title("🔍 Aurora MySQL Upgrade Readiness Checker")
    st.caption(t("page_caption"))
    _env_badge()

    with st.sidebar:
        _sidebar_reports()

    blocked = any(not v for v in REQUIRED_ENV.values()) or st.session_state.running
    col1, col2 = st.columns([3, 1])
    with col1:
        run_btn = st.button(
            t("btn_run"),
            type="primary",
            use_container_width=True,
            disabled=blocked,
        )
    with col2:
        if st.button(t("btn_clear"), use_container_width=True, disabled=st.session_state.running):
            st.session_state.update(
                terminal_output="",
                final_result=None,
                report_contents={},
                steps={},
            )
            st.rerun()

    st.subheader(t("progress_header"))
    step_slot = st.empty()
    _render_steps(step_slot)

    with st.expander(t("detail_log"), expanded=False):
        terminal_slot = st.empty()
        if st.session_state.terminal_output:
            terminal_slot.code(st.session_state.terminal_output, language=None)
        else:
            terminal_slot.code(t("terminal_idle"), language=None)

    if not run_btn:
        return

    st.session_state.update(
        running=True,
        final_result=None,
        report_contents={},
        terminal_output="",
        steps={},
    )
    payload = {
        "blue_host": BLUE_HOST,
        "green_host": GREEN_HOST,
        "db_secret_id": DB_SECRET_ID,
        "green_log_group": GREEN_LOG_GROUP,
        "error_log_analyzer_arn": ERROR_LOG_ANALYZER_ARN,
        "variables_compare_arn": VARIABLES_COMPARE_ARN,
        "upgrade_readiness_analyzer_arn": UPGRADE_READINESS_ARN,
        "query_risk_scorer_arn": QUERY_RISK_SCORER_ARN,
        "plan_diff_arn": PLAN_DIFF_ARN,
        "s3_bucket": S3_BUCKET,
        "region": AWS_REGION,
        "language": REPORT_LANGUAGE,
    }
    if DB_USER:  # optional override of the secret's username
        payload["db_user"] = DB_USER
    try:
        run_orchestrator(payload, step_slot, terminal_slot)
    except Exception as exc:  # noqa: BLE001
        st.session_state.terminal_output += t("run_error", exc=exc)
        terminal_slot.code(st.session_state.terminal_output, language=None)
    finally:
        st.session_state.running = False
        st.rerun()


if __name__ == "__main__":
    main()
