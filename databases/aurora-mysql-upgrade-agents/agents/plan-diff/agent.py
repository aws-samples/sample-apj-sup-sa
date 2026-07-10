"""
Aurora MySQL Plan Diff Agent

Pulls recent SELECT statements from the Blue cluster's
performance_schema.events_statements_history, runs EXPLAIN FORMAT=JSON for
each one on both Blue and Green, and compares the resulting plans field by
field. Plans that change between the two clusters are forwarded to the LLM
for explanation; identical plans only show up in the summary count.

Scoped to the Aurora MySQL 3.04 → 3.10 minor upgrade — checks whether the
optimizer picks materially different plans on the new minor version.

Requires Performance_schema to be enabled on the Blue cluster.
"""

import os
import json
import logging
import time
import uuid
import re
from datetime import datetime, timezone

import boto3
import pymysql
from botocore.config import Config as BotocoreConfig
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

AGENT_NAME = "aurora_plan_diff"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

INVOCATION_ID = str(uuid.uuid4())[:8]
logger.info(f"🔵 AGENT MODULE LOADED - Invocation ID: {INVOCATION_ID}")

app = BedrockAgentCoreApp()


# DB connection settings, populated by invoke() before the LLM runs; never
# passed through the prompt. The password is resolved from Secrets Manager
# (see _load_db_credentials), so it never travels in the invocation payload.
_credentials: dict = {}
# Report language, set by invoke() so save_report can localize the disclaimer.
_report_language: str = "ko"

# TLS: RDS/Aurora global CA bundle baked into the image by the Dockerfile.
RDS_CA_BUNDLE = "/app/rds-ca-bundle.pem"
# Connection timeouts (seconds): fail fast on bad endpoint / SG / provisioning.
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 30


def _load_db_credentials(secret_id: str, db_user: str | None = None) -> dict:
    """Resolve DB credentials from Secrets Manager.

    Expects an RDS-style secret ({"username", "password"} JSON). Returns
    {"user", "password"}. db_user, if given, overrides the secret's username.
    The plaintext password never leaves this process or appears in any payload.
    """
    region = os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("secretsmanager", region_name=region)
    raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
    data = json.loads(raw)
    return {
        "user": db_user or data.get("username", "admin"),
        "password": data["password"],
    }
# Target queries pulled from Blue's events_statements_history.
_target_queries: list[dict] = []
# Per-query EXPLAIN comparison results.
_compared: list[dict] = []
_report_urls = {"s3_url": None, "presigned_url": None, "raw_s3_url": None}


# Pulls the recent SELECT digests from Blue's history. Mirrors the user's
# original SQL exactly so the same set of literals (real values that hit the
# optimizer) is used downstream.
TARGET_QUERY_SQL = """
SELECT
    CURRENT_SCHEMA AS db_name,
    ROUND(TIMER_WAIT / 1000000000000, 3) AS exec_seconds,
    ROWS_EXAMINED AS rows_examined,
    ROWS_SENT AS rows_sent,
    TRIM(SQL_TEXT) AS sql_text
FROM
    performance_schema.events_statements_history
WHERE
    CURRENT_SCHEMA NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
    AND CURRENT_SCHEMA IS NOT NULL
    AND SQL_TEXT LIKE 'SELECT%'
    AND SQL_TEXT NOT LIKE 'SELECT 1%'
    AND SQL_TEXT NOT LIKE 'SELECT @@%'
ORDER BY
    TIMER_WAIT DESC
LIMIT 30
"""


# Plan-node fields that materially affect runtime. Walking the JSON
# recursively and pulling these out gives a stable shape for diffing —
# noise like cost numbers, eval-cost rounding, hex IDs is ignored.
_PLAN_DIFF_FIELDS = (
    "access_type",
    "key",
    "key_length",
    "used_key_parts",
    "ref",
    "rows_examined_per_scan",
    "rows_produced_per_join",
    "filtered",
    "using_index",
    "using_temporary_table",
    "using_filesort",
    "attached_condition",
    "materialized_from_subquery",
)


def _walk_plan(node, prefix=""):
    """Recursively yield (path, key, value) for every plan field of interest."""
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            if k in _PLAN_DIFF_FIELDS:
                # used_key_parts is a list — render stably for diff.
                yield (path, k, json.dumps(v, sort_keys=True, default=str))
            yield from _walk_plan(v, path)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk_plan(item, f"{prefix}[{i}]")


def _extract_plan_signature(plan_json: dict) -> dict:
    """Flatten the plan JSON into a {path: value} map of the diff fields."""
    if not plan_json:
        return {}
    sig = {}
    for path, _key, value in _walk_plan(plan_json.get("query_block", plan_json)):
        sig[path] = value
    return sig


def _diff_signatures(blue_sig: dict, green_sig: dict) -> list[dict]:
    """Return per-field differences. Empty list = identical plan shape."""
    diffs = []
    all_paths = set(blue_sig) | set(green_sig)
    for path in sorted(all_paths):
        b = blue_sig.get(path)
        g = green_sig.get(path)
        if b != g:
            diffs.append({"field": path, "blue": b, "green": g})
    return diffs


def log_token_usage(result, agent_name: str = AGENT_NAME):
    try:
        usage = getattr(result, "usage", None)
        if not usage:
            return
        if isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
        else:
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0
        logger.info(
            f"📊 [{INVOCATION_ID}] TOKEN_USAGE agent={agent_name} "
            f"input_tokens={input_tokens} output_tokens={output_tokens}"
        )
        cw = boto3.client("cloudwatch", region_name="us-east-1")
        cw.put_metric_data(
            Namespace="AgentCore/TokenUsage",
            MetricData=[
                {
                    "MetricName": name,
                    "Dimensions": [{"Name": "AgentName", "Value": agent_name}],
                    "Value": value,
                    "Unit": "Count",
                }
                for name, value in (
                    ("InputTokens", input_tokens),
                    ("OutputTokens", output_tokens),
                    ("TotalTokens", input_tokens + output_tokens),
                )
            ],
        )
    except Exception as e:
        logger.error(f"❌ [{INVOCATION_ID}] Failed to log token usage: {e}")


def _connect(host: str, port: int = 3306):
    """Open a TLS, timeout-bounded, read-only pymysql connection.

    Note: this agent runs EXPLAIN, so it does NOT force a read-only session
    (some EXPLAIN paths materialize temp tables). Use a least-privilege,
    read-only monitoring user + the reader endpoint in production instead.
    """
    return pymysql.connect(
        host=host,
        port=port,
        user=_credentials.get("user"),
        password=_credentials.get("password"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=_CONNECT_TIMEOUT,
        read_timeout=_READ_TIMEOUT,
        ssl_ca=RDS_CA_BUNDLE,
        ssl_verify_cert=True,
    )


@tool
def collect_target_queries(host: str, port: int = 3306) -> str:
    """
    Pull up to 30 recent heavy SELECT statements from Blue's
    performance_schema.events_statements_history. Caches the rows for the
    EXPLAIN comparison step.
    """
    global _target_queries
    logger.info(f"🔧 [{INVOCATION_ID}] TOOL collect_target_queries STARTED - host={host}")
    start = time.time()
    conn = None
    try:
        conn = _connect(host, port)
        with conn.cursor() as cur:
            cur.execute(TARGET_QUERY_SQL)
            rows = cur.fetchall()
        # Floats / Decimals → JSON-friendly types.
        normalized = []
        for r in rows:
            normalized.append(
                {
                    "db_name": r["db_name"],
                    "exec_seconds": float(r["exec_seconds"]) if r["exec_seconds"] is not None else None,
                    "rows_examined": int(r["rows_examined"] or 0),
                    "rows_sent": int(r["rows_sent"] or 0),
                    "sql_text": r["sql_text"],
                }
            )
        _target_queries = normalized
        elapsed = time.time() - start
        logger.info(
            f"✅ [{INVOCATION_ID}] TOOL collect_target_queries COMPLETED - "
            f"{elapsed:.2f}s - {len(normalized)} queries"
        )
        return f"✅ Collected {len(normalized)} target SELECT queries from {host}"
    except pymysql.err.OperationalError as e:
        # Connection / auth / TLS class — log detail, return a generic message
        # (no host or driver internals leaked to the LLM output).
        elapsed = time.time() - start
        logger.error(
            f"❌ [{INVOCATION_ID}] TOOL collect_target_queries DB connection failed - "
            f"{elapsed:.2f}s - {type(e).__name__}: {e}"
        )
        return "❌ collect_target_queries failed: could not connect to the database (check endpoint, security group, TLS, and credentials)."
    except Exception as e:
        elapsed = time.time() - start
        logger.error(
            f"❌ [{INVOCATION_ID}] TOOL collect_target_queries FAILED - {elapsed:.2f}s - {type(e).__name__}: {e}"
        )
        return f"❌ collect_target_queries failed: {type(e).__name__} while querying performance_schema."
    finally:
        if conn is not None:
            conn.close()


@tool
def explain_on_both(blue_host: str, green_host: str, port: int = 3306) -> str:
    """
    Run EXPLAIN FORMAT=JSON on every cached target query against both
    clusters, parse plans, compute a per-field diff, and store everything
    in module memory. Returns a short summary; the LLM should call
    summarize_plan_diffs for the curated input.
    """
    global _compared
    if not _target_queries:
        return "❌ No target queries collected. Run collect_target_queries first."

    logger.info(
        f"🔧 [{INVOCATION_ID}] TOOL explain_on_both STARTED - "
        f"blue={blue_host} green={green_host} count={len(_target_queries)}"
    )
    start = time.time()

    blue_conn = green_conn = None
    try:
        blue_conn = _connect(blue_host, port)
        green_conn = _connect(green_host, port)

        compared = []
        for idx, q in enumerate(_target_queries, start=1):
            db_name = q["db_name"]
            sql = q["sql_text"]
            entry = {
                "id": idx,
                "db_name": db_name,
                "sql_text": sql,
                "blue_plan": None,
                "green_plan": None,
                "blue_error": None,
                "green_error": None,
                "diff": [],
                "verdict": "unknown",
            }

            for label, conn in (("blue", blue_conn), ("green", green_conn)):
                try:
                    with conn.cursor() as cur:
                        # Use the same schema the original query ran in.
                        cur.execute(f"USE `{db_name}`")
                        cur.execute(f"EXPLAIN FORMAT=JSON {sql}")
                        row = cur.fetchone()
                    raw = next(iter(row.values())) if row else None
                    plan = json.loads(raw) if raw else None
                    entry[f"{label}_plan"] = plan
                except Exception as e:
                    entry[f"{label}_error"] = str(e)

            if entry["blue_error"] or entry["green_error"]:
                entry["verdict"] = "explain_failed"
            else:
                blue_sig = _extract_plan_signature(entry["blue_plan"])
                green_sig = _extract_plan_signature(entry["green_plan"])
                entry["diff"] = _diff_signatures(blue_sig, green_sig)
                entry["verdict"] = "changed" if entry["diff"] else "identical"

            compared.append(entry)

        _compared = compared
        elapsed = time.time() - start
        identical = sum(1 for c in compared if c["verdict"] == "identical")
        changed = sum(1 for c in compared if c["verdict"] == "changed")
        failed = sum(1 for c in compared if c["verdict"] == "explain_failed")
        logger.info(
            f"✅ [{INVOCATION_ID}] TOOL explain_on_both COMPLETED - "
            f"{elapsed:.2f}s - identical={identical} changed={changed} failed={failed}"
        )
        return (
            f"✅ Compared {len(compared)} queries: "
            f"identical={identical}, changed={changed}, failed={failed}"
        )
    except Exception as e:
        elapsed = time.time() - start
        logger.error(
            f"❌ [{INVOCATION_ID}] TOOL explain_on_both FAILED - {elapsed:.2f}s - {e}"
        )
        return f"❌ explain_on_both failed: {e}"
    finally:
        if blue_conn:
            blue_conn.close()
        if green_conn:
            green_conn.close()


@tool
def summarize_plan_diffs(sql_truncate: int = 240) -> str:
    """
    Return only the queries with verdict == 'changed' or 'explain_failed',
    with their diff field-list trimmed for LLM consumption. Identical
    queries are summarized in counts only — they don't need explanation.
    """
    if not _compared:
        return "❌ No comparisons yet. Run explain_on_both first."

    identical = [c for c in _compared if c["verdict"] == "identical"]
    changed = [c for c in _compared if c["verdict"] == "changed"]
    failed = [c for c in _compared if c["verdict"] == "explain_failed"]

    def _trim(c):
        return {
            "id": c["id"],
            "db_name": c["db_name"],
            "sql_text": (c["sql_text"][:sql_truncate] + " ...")
            if len(c["sql_text"]) > sql_truncate
            else c["sql_text"],
            "diff": c["diff"],
            "blue_error": c["blue_error"],
            "green_error": c["green_error"],
        }

    payload = {
        "totals": {
            "total": len(_compared),
            "identical": len(identical),
            "changed": len(changed),
            "explain_failed": len(failed),
        },
        "changed": [_trim(c) for c in changed],
        "explain_failed": [_trim(c) for c in failed],
        # identical 의 SQL 만 짧게 노출. plan diff 필요 없음.
        "identical_sample": [
            {
                "id": c["id"],
                "db_name": c["db_name"],
                "sql_text": (c["sql_text"][:sql_truncate] + " ...")
                if len(c["sql_text"]) > sql_truncate
                else c["sql_text"],
            }
            for c in identical[:5]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


# Timestamp labels the LLM tends to emit (KO + EN), used to strip any
# generation date/time from report bodies. The S3 object name already carries
# a real timestamp, so an LLM-invented date in the body is pure noise.
_TIMESTAMP_LABELS = (
    "생성 일시", "수집 일시", "분석 일시", "작성 일시",
    "Report generated", "Generated at", "Generated on",
    "Collected at", "Analyzed at", "Date generated",
)


def _strip_report_time(markdown: str) -> str:
    """Remove any generation/collection date line the LLM added to the report.

    The prompt tells the model not to print a date, but LLMs add one anyway (and
    hallucinate the year). We delete it deterministically:
      - a standalone bold line like `**수집 일시:** ...`  → drop the whole line
      - an inline segment like `... | 수집 일시: X | ...`  → drop just that segment
    Only timestamp-labeled text is touched, so data values are never altered.
    """
    labels = "|".join(re.escape(x) for x in _TIMESTAMP_LABELS)
    # inline segment followed by a pipe: drop the segment + that pipe (keep the leading "| ")
    markdown = re.sub(rf"\*?\*?(?:{labels})\*?\*?\s*[:：][^|\n]*\|\s*", "", markdown)
    # trailing inline segment (no pipe after): drop the leading " |" too
    markdown = re.sub(rf"\s*\|\s*\*?\*?(?:{labels})\*?\*?\s*[:：][^|\n]*", "", markdown)
    # standalone line
    markdown = re.sub(rf"(?m)^\s*\*?\*?(?:{labels})\*?\*?\s*[:：].*\n?", "", markdown)
    return markdown


# LLM-generated reports must be reviewed by a human DB expert. We prepend this
# deterministically in save_report (rather than trusting the LLM to include it).
_DISCLAIMER = {
    "ko": "> ⚠️ 본 리포트는 LLM(생성형 AI)이 자동 생성한 분석 결과입니다. 실제 업그레이드 적용 전 반드시 DB 전문가의 검토를 거치시기 바랍니다.",
    "en": "> ⚠️ This report was automatically generated by an LLM (generative AI). Please have a database expert review it before applying any upgrade.",
}


def _prepend_disclaimer(markdown: str, language: str) -> str:
    """Prepend the review disclaimer above the report's first heading."""
    note = _DISCLAIMER.get(language) or _DISCLAIMER["ko"]
    return f"{note}\n\n{markdown.lstrip()}"


@tool
def save_report(
    markdown_content: str,
    s3_bucket: str = "aurora-mysql-upgrade-reports",
    s3_key_prefix: str = "plan-diff",
) -> str:
    """
    Save the markdown report and the full raw comparison JSON (both plans
    per query) to S3, generate presigned URLs, and update module storage.
    """
    global _report_urls
    logger.info(
        f"🔧 [{INVOCATION_ID}] TOOL save_report STARTED - bucket={s3_bucket}, "
        f"content_length={len(markdown_content)}, raw_rows={len(_compared)}"
    )
    start = time.time()
    try:
        # Strip any generation date the LLM added — the S3 object name already
        # carries a real timestamp, and LLM-written dates are unreliable.
        markdown_content = _strip_report_time(markdown_content)
        # Prepend the human-review disclaimer (deterministic, not LLM-authored).
        markdown_content = _prepend_disclaimer(markdown_content, _report_language)

        s3 = boto3.client("s3", region_name="us-east-1")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_key = f"{s3_key_prefix}/plan_diff_report_{timestamp}.md"
        raw_key = f"{s3_key_prefix}/plan_diff_raw_{timestamp}.json"

        s3.put_object(
            Bucket=s3_bucket,
            Key=md_key,
            Body=markdown_content.encode("utf-8"),
            ContentType="text/markdown",
        )
        s3.put_object(
            Bucket=s3_bucket,
            Key=raw_key,
            Body=json.dumps(_compared, ensure_ascii=False, default=str).encode("utf-8"),
            ContentType="application/json",
        )

        s3_url = f"s3://{s3_bucket}/{md_key}"
        raw_s3_url = f"s3://{s3_bucket}/{raw_key}"
        presigned = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": s3_bucket, "Key": md_key},
            ExpiresIn=3600,
        )
        _report_urls.update(
            {"s3_url": s3_url, "presigned_url": presigned, "raw_s3_url": raw_s3_url}
        )
        elapsed = time.time() - start
        logger.info(
            f"✅ [{INVOCATION_ID}] TOOL save_report COMPLETED - {elapsed:.2f}s - {s3_url}"
        )
        return json.dumps(
            {"s3_url": s3_url, "presigned_url": presigned, "raw_s3_url": raw_s3_url}
        )
    except Exception as e:
        elapsed = time.time() - start
        logger.error(
            f"❌ [{INVOCATION_ID}] TOOL save_report FAILED - {elapsed:.2f}s - {e}"
        )
        return f"ERROR: {e}"


def build_language_directive(language: str) -> str:
    """Return an output-language instruction block prepended to the agent prompt.

    The report template further down uses Korean headings as a structural guide;
    for non-Korean languages the model is told to translate every visible label.
    """
    if (language or "ko").strip().lower() == "en":
        return (
            "**Output language: English**\n"
            "- Write the ENTIRE report in English: title, all section headings, "
            "table headers, descriptions, and recommendations.\n"
            "- The report structure shown below uses Korean headings only as a "
            "layout guide — translate every Korean label/heading into natural "
            "English while keeping the same structure.\n"
            "- Keep MySQL/Aurora identifiers, plan field names, SQL, and metric "
            "names unchanged.\n"
        )
    return (
        "**출력 언어: 한국어**\n"
        "- 리포트 전체(제목, 모든 섹션 헤더, 표 헤더, 설명, 권고)를 한국어로 작성하세요.\n"
    )


@app.entrypoint
async def invoke(payload):
    """
    Expected payload:
    {
        "blue_host":  "<blue>.cluster-xxxxx.us-east-1.rds.amazonaws.com",
        "green_host": "<green>.cluster-xxxxx.us-east-1.rds.amazonaws.com",
        "db_secret_id": "<Secrets Manager secret name or ARN>",
        "db_user":    "admin",                   # optional, overrides secret username
        "s3_bucket":  "aurora-mysql-upgrade-reports"  # optional
    }
    """
    invoke_start = time.time()
    logger.info(f"🚀 [{INVOCATION_ID}] ========== INVOKE FUNCTION STARTED ==========")
    logger.info(f"📥 [{INVOCATION_ID}] Received payload keys: {list(payload.keys())}")

    blue_host = payload.get("blue_host")
    green_host = payload.get("green_host")
    db_secret_id = payload.get("db_secret_id")
    db_user = payload.get("db_user")  # optional override of the secret's username
    s3_bucket = payload.get("s3_bucket", "aurora-mysql-upgrade-reports")
    language = payload.get("language", "ko")  # "ko" or "en"
    global _report_language
    _report_language = language
    language_directive = build_language_directive(language)

    if not all([blue_host, green_host, db_secret_id]):
        return {
            "status": "error",
            "message": "Missing required configuration: blue_host, green_host, db_secret_id are required",
        }

    global _credentials
    try:
        _credentials = _load_db_credentials(db_secret_id, db_user)
    except Exception as e:
        logger.error(f"❌ [{INVOCATION_ID}] Failed to load DB credentials from Secrets Manager: {type(e).__name__}")
        return {
            "status": "error",
            "message": "Failed to load DB credentials from Secrets Manager. Check db_secret_id and the runtime role's secretsmanager:GetSecretValue permission.",
        }

    agent_message = f"""
{language_directive}
Aurora MySQL 3.04 → 3.10 마이너 업그레이드 시 옵티마이저 plan 변동을 실측으로 확인합니다.
Blue 클러스터의 최근 무거운 SELECT 쿼리 30개를 가져와, 같은 SQL 을 Blue/Green 양쪽에서
EXPLAIN FORMAT=JSON 으로 실행한 뒤 plan 핵심 필드를 비교한 결과를 마크다운 리포트로
저장하세요.

**작업 순서:**
1. collect_target_queries 로 host="{blue_host}" 의 events_statements_history 에서 SELECT 30개 수집
   - 자격증명은 도구 내부에서 자동 처리 (인자로 전달하지 마세요)
2. explain_on_both 으로 blue_host="{blue_host}", green_host="{green_host}" 양쪽에서 EXPLAIN 실행
   - 도구가 핵심 필드(access_type, key, key_length, used_key_parts, rows_examined_per_scan,
     rows_produced_per_join, filtered, using_index, using_temporary_table, using_filesort,
     attached_condition, materialized_from_subquery) 를 비교해 verdict 부여
3. summarize_plan_diffs 로 변경된(changed)/실패(explain_failed) 쿼리만 받아 분석
4. save_report 로 저장 (s3_bucket="{s3_bucket}")
   - 응답에 presigned_url 포함

**리포트 작성 규칙:**
- 출력 언어는 위의 "출력 언어" 지시를 따를 것, 간결한 문장
- **리포트에 생성/수집 일시·날짜를 넣지 말 것 (예: "수집 일시", "Report generated" 등 금지).**
- identical 은 카운트만 — 본문에 SQL 나열 X (예: "동일: 18건")
- changed 만 본문 표/케이스에 등장. 각 쿼리마다:
  · 짧은 SQL 미리보기
  · 핵심 필드 차이를 표로 (컬럼: 필드명 / Blue / Green)
  · 1~2줄 권고 (인덱스 / 힌트 / regression 위험도 인지)
- explain_failed 가 있으면 별도 섹션에 사유 표시

**리포트 구조:**
```markdown
# Aurora MySQL Plan Diff 리포트 (Blue vs Green)

**대상:** Blue={blue_host}, Green={green_host}
**대상 쿼리:** events_statements_history 상위 30개 SELECT

## 1. 요약
- 전체 비교: N건
- 동일: X건
- 변경: Y건  ← 본 리포트의 주요 분석 대상
- EXPLAIN 실패: Z건

## 2. plan 이 변경된 쿼리
각 케이스마다 ### 2.x 로 분리.

### 2.1 [DB: foo] SELECT ... (id=1)
**SQL 미리보기**
```sql
SELECT ...
```
**Plan 차이**
| 필드 | Blue | Green |
|------|------|-------|
| query_block.table.access_type | "ref" | "range" |
| query_block.table.key | "idx_a" | "idx_b" |
| ... | ... | ... |

**권고:** (1~2줄)

## 3. EXPLAIN 실패 쿼리
| id | DB | 사유 |
|----|----|------|
| ... | ... | ... |

## 4. 마이너 업그레이드 액션
- changed 가 있다면: 운영 트래픽으로 새 plan 의 latency 비교 → regression 시 hint 또는
  optimizer_switch 로 plan 고정 검토
- changed 가 0건이면: 옵티마이저 차원 이슈 가능성 낮음. 다른 점검(InnoDB / 변수 / error log)
  결과 우선순위로 진행
```

**중요:** 마지막에 반드시 save_report 를 호출해서 리포트와 raw 데이터를 S3 에 저장하세요.
"""

    logger.info(f"🤖 [{INVOCATION_ID}] ========== CREATING agent ==========")
    bedrock_model = BedrockModel(
        model_id=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        region_name="us-east-1",
        boto_client_config=BotocoreConfig(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=60,
            read_timeout=300,
        ),
    )
    agent = Agent(
        tools=[collect_target_queries, explain_on_both, summarize_plan_diffs, save_report],
        model=bedrock_model,
    )

    logger.info(f"🤖 [{INVOCATION_ID}] ========== CALLING agent.invoke_async() ==========")
    agent_start = time.time()
    result = await agent.invoke_async(agent_message)
    logger.info(
        f"✅ [{INVOCATION_ID}] ========== agent() RETURNED - "
        f"{time.time() - agent_start:.2f}s =========="
    )

    log_token_usage(result, AGENT_NAME)

    identical = sum(1 for c in _compared if c["verdict"] == "identical")
    changed = sum(1 for c in _compared if c["verdict"] == "changed")
    failed = sum(1 for c in _compared if c["verdict"] == "explain_failed")

    response = {
        "status": "success",
        "blue_host": blue_host,
        "green_host": green_host,
        "result": result.message,
        "queries_collected": len(_target_queries),
        "totals": {
            "identical": identical,
            "changed": changed,
            "explain_failed": failed,
        },
    }
    if _report_urls.get("presigned_url"):
        response["presigned_url"] = _report_urls["presigned_url"]
        response["s3_url"] = _report_urls["s3_url"]
        response["raw_s3_url"] = _report_urls["raw_s3_url"]

    logger.info(
        f"🏁 [{INVOCATION_ID}] ========== INVOKE FUNCTION COMPLETED - "
        f"{time.time() - invoke_start:.2f}s =========="
    )
    return response


if __name__ == "__main__":
    app.run()
