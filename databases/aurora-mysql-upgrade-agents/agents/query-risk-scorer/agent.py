"""
Aurora MySQL Query Risk Scorer Agent

Runs a performance_schema digest query on the Blue cluster, computes a
weighted upgrade-risk score per digest (heavy disk tmp tables, full scans,
sort volume, no-good-index usage), classifies each digest by SQL pattern
that is sensitive to optimizer behavior shifts, and produces a markdown
report scoped to the Aurora MySQL 3.04 → 3.10 minor upgrade.

Requires Performance_schema to be enabled on the target cluster.
"""

import json
import os
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

# Agent name for metrics tracking
AGENT_NAME = "aurora_query_risk_scorer"

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
# Risk rows captured by collect_query_risks; consumed by get_top_risks.
_risk_rows: list[dict] = []

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


def _connect(host: str, port: int = 3306):
    """Open a TLS, timeout-bounded, read-only pymysql connection."""
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
        init_command="SET SESSION TRANSACTION READ ONLY",
    )
# Report URLs set by save_report.
_report_urls = {"s3_url": None, "presigned_url": None, "raw_s3_url": None}


# Same query the user provided. Aliases are Korean to match the agreed
# report vocabulary; the LLM consumes the dict by key name.
RISK_QUERY = """
SELECT
    NOW() AS collected_at,
    @@version AS mysql_version,
    base.DIGEST AS '쿼리_식별_해시',
    base.SCHEMA_NAME AS 'DB_명',
    base.COUNT_STAR AS '누적_실행_횟수',
    ROUND(base.SUM_TIMER_WAIT / 1000000000000, 2) AS '누적_수행시간_초',
    ROUND(base.AVG_TIMER_WAIT / 1000000000, 3) AS '평균_수행시간_ms',
    base.SUM_ROWS_EXAMINED AS '검사한_총_Row수',
    base.SUM_ROWS_SENT AS '반환한_총_Row수',
    ROUND(
        base.SUM_ROWS_EXAMINED / NULLIF(base.SUM_ROWS_SENT, 0),
        2
    ) AS '건당_평균_스캔_Row수',
    base.SUM_CREATED_TMP_TABLES AS '메모리_임시테이블_생성수',
    base.SUM_CREATED_TMP_DISK_TABLES AS '디스크_임시테이블_생성수',
    base.SUM_SORT_ROWS AS '정렬된_총_Row수',
    base.SUM_NO_INDEX_USED AS '인덱스_미사용_횟수',
    base.SUM_NO_GOOD_INDEX_USED AS '부적절한_인덱스_사용_횟수',
    (
        (base.SUM_CREATED_TMP_DISK_TABLES * 20)
        + (base.SUM_NO_GOOD_INDEX_USED * 50)
        + (base.SUM_SORT_ROWS / 1000)
        + (base.SUM_ROWS_EXAMINED / 10000)
    ) AS '업그레이드_위험도_점수',
    CASE
        WHEN base.DIGEST_TEXT LIKE '%JOIN%' AND base.DIGEST_TEXT LIKE '%ORDER BY%' AND base.DIGEST_TEXT LIKE '%LIMIT%' THEN 'JOIN_ORDERBY_LIMIT'
        WHEN base.DIGEST_TEXT LIKE '%GROUP BY%'   THEN 'GROUP_BY'
        WHEN base.DIGEST_TEXT LIKE '%DISTINCT%'   THEN 'DISTINCT'
        WHEN base.DIGEST_TEXT LIKE '% EXISTS %'   THEN 'EXISTS_SUBQUERY'
        WHEN base.DIGEST_TEXT LIKE '% IN ( SELECT%' THEN 'IN_SUBQUERY'
        WHEN base.DIGEST_TEXT LIKE 'WITH %'        THEN 'CTE'
        WHEN base.DIGEST_TEXT LIKE '%OVER (%'      THEN 'WINDOW_FUNCTION'
        WHEN base.DIGEST_TEXT LIKE '%JSON_%'       THEN 'JSON_FUNCTION'
        WHEN base.DIGEST_TEXT LIKE '% OR %'        THEN 'OR_CONDITION'
        WHEN base.DIGEST_TEXT LIKE '%UNION%'       THEN 'UNION'
        WHEN base.DIGEST_TEXT LIKE '%UPDATE%JOIN%' THEN 'UPDATE_JOIN'
        WHEN base.DIGEST_TEXT LIKE '%DELETE%JOIN%' THEN 'DELETE_JOIN'
        ELSE 'OTHER_OPTIMIZER_TARGET'
    END AS '예상_영향_타입',
    LEFT(base.DIGEST_TEXT, 500) AS '정규화된_SQL_샘플'
FROM (
    SELECT
        DIGEST, SCHEMA_NAME, COUNT_STAR, SUM_TIMER_WAIT, AVG_TIMER_WAIT,
        SUM_ROWS_EXAMINED, SUM_ROWS_SENT, SUM_CREATED_TMP_TABLES,
        SUM_CREATED_TMP_DISK_TABLES, SUM_SORT_ROWS, SUM_NO_INDEX_USED,
        SUM_NO_GOOD_INDEX_USED, DIGEST_TEXT
    FROM
        performance_schema.events_statements_summary_by_digest
    WHERE
        DIGEST IS NOT NULL
        AND SCHEMA_NAME NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
        AND DIGEST_TEXT NOT LIKE 'SHOW %'
        AND DIGEST_TEXT NOT LIKE 'SET %'
        AND DIGEST_TEXT NOT LIKE 'COMMIT%'
        AND DIGEST_TEXT NOT LIKE 'ROLLBACK%'
        AND COUNT_STAR >= 30
        AND SUM_TIMER_WAIT > 0
    ORDER BY
        SUM_TIMER_WAIT DESC
    LIMIT 500
) base
ORDER BY
    `업그레이드_위험도_점수` DESC,
    base.SUM_TIMER_WAIT DESC
"""


def log_token_usage(result, agent_name: str = AGENT_NAME):
    """Emit Strands token usage to CloudWatch (best-effort)."""
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
        cloudwatch = boto3.client("cloudwatch", region_name="us-east-1")
        cloudwatch.put_metric_data(
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


@tool
def collect_query_risks(host: str, port: int = 3306) -> str:
    """
    Run the digest-based risk-scoring query against the target cluster and
    cache the result in module storage.

    Args:
        host: Database host (cluster endpoint preferred).
        port: Database port (default 3306).

    Returns:
        Short status message with the row count collected.
    """
    global _risk_rows
    logger.info(f"🔧 [{INVOCATION_ID}] TOOL collect_query_risks STARTED - host={host}")
    start = time.time()
    connection = None
    try:
        connection = _connect(host, port)
        with connection.cursor() as cursor:
            cursor.execute(RISK_QUERY)
            rows = cursor.fetchall()

        # Stringify datetime / Decimal so we can JSON-serialize later.
        normalized = []
        for row in rows:
            normalized_row = {}
            for k, v in row.items():
                if isinstance(v, datetime):
                    normalized_row[k] = v.isoformat()
                elif hasattr(v, "is_finite"):  # Decimal
                    normalized_row[k] = float(v)
                else:
                    normalized_row[k] = v
            normalized.append(normalized_row)

        _risk_rows = normalized
        elapsed = time.time() - start
        logger.info(
            f"✅ [{INVOCATION_ID}] TOOL collect_query_risks COMPLETED - "
            f"{elapsed:.2f}s - {len(normalized)} rows"
        )
        return f"✅ Collected {len(normalized)} risk-scored digests from {host}"
    except pymysql.err.OperationalError as e:
        elapsed = time.time() - start
        logger.error(
            f"❌ [{INVOCATION_ID}] TOOL collect_query_risks DB connection failed - "
            f"{elapsed:.2f}s - {type(e).__name__}: {e}"
        )
        return "❌ collect_query_risks failed: could not connect to the database (check endpoint, security group, TLS, and credentials)."
    except Exception as e:
        elapsed = time.time() - start
        logger.error(
            f"❌ [{INVOCATION_ID}] TOOL collect_query_risks FAILED - {elapsed:.2f}s - {type(e).__name__}: {e}"
        )
        return f"❌ collect_query_risks failed: {type(e).__name__} while querying performance_schema."
    finally:
        if connection is not None:
            connection.close()


@tool
def get_top_risks(top_n: int = 30) -> str:
    """
    Return the top-N risk-scored digests (already sorted by the SQL).
    Use this — never the full set — as input for the LLM analysis.

    Args:
        top_n: Number of digests to return (default 30).

    Returns:
        JSON string with metadata + top rows.
    """
    if not _risk_rows:
        return "❌ No data collected yet. Run collect_query_risks first."
    top = _risk_rows[: max(1, int(top_n))]
    summary = {
        "total_collected": len(_risk_rows),
        "returned": len(top),
        "top_rows": top,
    }
    return json.dumps(summary, ensure_ascii=False, default=str)


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
    s3_key_prefix: str = "query-risk",
) -> str:
    """
    Save the markdown report and the raw collected rows (JSON) to S3 and
    generate presigned URLs for both.
    """
    global _report_urls
    logger.info(
        f"🔧 [{INVOCATION_ID}] TOOL save_report STARTED - bucket={s3_bucket}, "
        f"content_length={len(markdown_content)}, raw_rows={len(_risk_rows)}"
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
        md_key = f"{s3_key_prefix}/query_risk_report_{timestamp}.md"
        raw_key = f"{s3_key_prefix}/query_risk_raw_{timestamp}.json"

        s3.put_object(
            Bucket=s3_bucket,
            Key=md_key,
            Body=markdown_content.encode("utf-8"),
            ContentType="text/markdown",
        )
        s3.put_object(
            Bucket=s3_bucket,
            Key=raw_key,
            Body=json.dumps(_risk_rows, ensure_ascii=False, default=str).encode("utf-8"),
            ContentType="application/json",
        )

        s3_url = f"s3://{s3_bucket}/{md_key}"
        raw_s3_url = f"s3://{s3_bucket}/{raw_key}"

        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": s3_bucket, "Key": md_key},
            ExpiresIn=3600,
        )

        _report_urls.update(
            {"s3_url": s3_url, "presigned_url": presigned_url, "raw_s3_url": raw_s3_url}
        )
        elapsed = time.time() - start
        logger.info(
            f"✅ [{INVOCATION_ID}] TOOL save_report COMPLETED - {elapsed:.2f}s - {s3_url}"
        )
        return json.dumps(
            {"s3_url": s3_url, "presigned_url": presigned_url, "raw_s3_url": raw_s3_url}
        )
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"❌ [{INVOCATION_ID}] TOOL save_report FAILED - {elapsed:.2f}s - {e}")
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
            "- Keep MySQL/Aurora identifiers, variable names, SQL, and metric "
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
        "blue_host": "<blue>.cluster-xxxxx.us-east-1.rds.amazonaws.com",
        "db_secret_id": "<Secrets Manager secret name or ARN>",
        "db_user":   "admin",                   # optional, overrides secret username
        "top_n":     30,                        # optional
        "s3_bucket": "aurora-mysql-upgrade-reports"  # optional
    }
    """
    invoke_start = time.time()
    logger.info(f"🚀 [{INVOCATION_ID}] ========== INVOKE FUNCTION STARTED ==========")
    logger.info(f"📥 [{INVOCATION_ID}] Received payload keys: {list(payload.keys())}")

    blue_host = payload.get("blue_host")
    db_secret_id = payload.get("db_secret_id")
    db_user = payload.get("db_user")  # optional override of the secret's username
    top_n = int(payload.get("top_n", 30))
    s3_bucket = payload.get("s3_bucket", "aurora-mysql-upgrade-reports")
    language = payload.get("language", "ko")  # "ko" or "en"
    global _report_language
    _report_language = language
    language_directive = build_language_directive(language)

    if not all([blue_host, db_secret_id]):
        return {
            "status": "error",
            "message": "Missing required configuration: blue_host and db_secret_id are required",
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
Aurora MySQL 3.04 → 3.10 마이너 업그레이드 시 plan 변동 위험이 큰 쿼리를 식별하고
마크다운 리포트로 저장하세요.

**컨텍스트:** 메이저 옵티마이저 교체는 없지만, 마이너 패치에 묶인 비용 추정/실행
계획의 미세한 변동, InnoDB 동작 보정, Aurora 옵션 default 변경이 일부 쿼리의
실행계획을 흔들 수 있습니다. performance_schema 의 digest 통계를 기반으로
실측 위험도(디스크 임시테이블, no-good-index, 정렬량, 검사 행수)를 가중 합산해
상위 위험 쿼리를 추려냅니다.

**작업 순서:**
1. collect_query_risks 로 host="{blue_host}" 에서 위험 점수 행 수집
   - 자격증명은 도구 내부에서 자동 처리 (인자로 전달하지 마세요)
2. get_top_risks 로 top_n={top_n} 만큼 행 조회 (이미 위험도 내림차순 정렬됨)
3. 분석 후 save_report 로 저장 (s3_bucket="{s3_bucket}")
   - save_report 가 반환하는 presigned_url 을 최종 응답에 포함

**리포트 작성 규칙:**
- 출력 언어는 위의 "출력 언어" 지시를 따를 것, 간결한 문장
- **리포트에 생성/수집 일시·날짜를 넣지 말 것 (예: "수집 일시", "Report generated" 등 금지).**
- "예상_영향_타입" 으로 행을 묶어 패턴별 핵심 메시지 제시
- 각 쿼리에 대한 권고는 1~2줄로 짧게: 인덱스/힌트/리팩토링 우선순위만
- raw 데이터(json)는 별도 객체로 함께 저장됨 — 리포트는 상위 N개에 집중
- 표에는 "정규화된_SQL_샘플" 을 한 줄로 잘라 넣을 것 (필요시 80자 cut)

**리포트 구조 (markdown):**
```markdown
# Aurora MySQL Query Risk 분석 리포트

**대상:** {blue_host} | **상위 N:** {top_n}

## 1. 요약
- 분석 대상 digest: 전체 X 개 / 상위 N 개 리포트 포함
- 가장 빈번한 위험 패턴: (예) GROUP_BY (12), JOIN_ORDERBY_LIMIT (7), ...
- 최우선 점검 권고 3 개

## 2. 패턴별 위험 쿼리
각 "예상_영향_타입" 마다 한 묶음. 최소 1행 이상 등장한 타입만 표시.

### 2.1 <패턴명> (N건)
한 줄 요약: 이 패턴이 마이너 업그레이드 시 왜 plan 변동에 민감한지.

| 위험도 | DB | 누적실행 | 평균(ms) | 검사Row | NoGoodIdx | 디스크TmpTbl | SQL 샘플 |
|--------|----|---------:|---------:|--------:|----------:|------------:|----------|
| 12345 | foo |   1,200 |   480.5 |  9.4M |  1,200 |  300 | SELECT ... |

권고:
- (예) `idx_foo_bar(col1, col2)` 검토
- (예) optimizer hint `JOIN_ORDER` 로 plan 고정 검토

## 3. 업그레이드 전 점검 액션
- 위 표 상위 5개 쿼리에 대해 EXPLAIN 비교 (3.04 vs 3.10)
- regression 발견 시 `optimizer_switch` 또는 hint 로 plan 고정
- raw 데이터: (S3 raw_s3_url 안내, 본문에는 굳이 URL 적지 않음)
```

**중요:** 마지막에 반드시 save_report 를 호출해 리포트를 S3 에 저장하세요.
"""

    # Bedrock streaming can take longer than the boto3 default 60s.
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
        tools=[collect_query_risks, get_top_risks, save_report],
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

    response = {
        "status": "success",
        "blue_host": blue_host,
        "result": result.message,
        "rows_collected": len(_risk_rows),
        "top_n": top_n,
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
