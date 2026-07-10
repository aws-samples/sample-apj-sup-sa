"""
MySQL Upgrade Readiness Analyzer Agent
Analyzes Blue instance for MySQL 8.4 upgrade readiness:
1. InnoDB Status Analysis - SHOW ENGINE INNODB STATUS
2. Query Optimizer Risk Analysis - performance_schema queries

Requires Performance_schema to be enabled.
"""

import os
import re
import pymysql
import logging
import time
import uuid
import json
import boto3
from datetime import datetime
from strands import Agent, tool
from strands.models import BedrockModel
from botocore.config import Config as BotocoreConfig
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Agent name for metrics tracking
AGENT_NAME = "rds_mysql_upgrade_readiness_analyzer"

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Generate unique invocation ID for tracking
INVOCATION_ID = str(uuid.uuid4())[:8]
logger.info(f"🔵 AGENT MODULE LOADED - Invocation ID: {INVOCATION_ID}")

# Initialize AgentCore App
app = BedrockAgentCoreApp()


def log_token_usage(result, agent_name: str = AGENT_NAME):
    """
    Extract and log token usage from Strands Agent result.
    Emits CloudWatch metrics for token tracking across sub-agents.
    """
    try:
        # Extract usage from result
        usage = getattr(result, 'usage', None)
        if not usage:
            logger.warning(f"📊 [{INVOCATION_ID}] No usage data in result")
            return

        input_tokens = getattr(usage, 'input_tokens', 0) or usage.get('input_tokens', 0) if isinstance(usage, dict) else 0
        output_tokens = getattr(usage, 'output_tokens', 0) or usage.get('output_tokens', 0) if isinstance(usage, dict) else 0

        # Log to stdout (will appear in CloudWatch Logs)
        logger.info(f"📊 [{INVOCATION_ID}] TOKEN_USAGE agent={agent_name} input_tokens={input_tokens} output_tokens={output_tokens}")

        # Emit CloudWatch metrics
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')

        # Put metrics with agent name dimension for filtering
        cloudwatch.put_metric_data(
            Namespace='AgentCore/TokenUsage',
            MetricData=[
                {
                    'MetricName': 'InputTokens',
                    'Dimensions': [
                        {'Name': 'AgentName', 'Value': agent_name},
                    ],
                    'Value': input_tokens,
                    'Unit': 'Count'
                },
                {
                    'MetricName': 'OutputTokens',
                    'Dimensions': [
                        {'Name': 'AgentName', 'Value': agent_name},
                    ],
                    'Value': output_tokens,
                    'Unit': 'Count'
                },
                {
                    'MetricName': 'TotalTokens',
                    'Dimensions': [
                        {'Name': 'AgentName', 'Value': agent_name},
                    ],
                    'Value': input_tokens + output_tokens,
                    'Unit': 'Count'
                }
            ]
        )
        logger.info(f"✅ [{INVOCATION_ID}] Token metrics emitted to CloudWatch: AgentCore/TokenUsage")

    except Exception as e:
        logger.error(f"❌ [{INVOCATION_ID}] Failed to log token usage: {str(e)}")


# Global storage for collected data and report URLs
_innodb_status = None
_query_risk_data = None
_report_urls = {"s3_url": None, "presigned_url": None}
# DB connection settings, populated by invoke() before the LLM runs and looked
# up by tools — never passed through the LLM prompt. The password is resolved
# from Secrets Manager (see _load_db_credentials), so it never travels in the
# invocation payload.
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


@tool
def check_performance_schema(host: str, port: int = 3306) -> str:
    """
    Check if Performance_schema is enabled on the MySQL instance

    Args:
        host: Database host (RDS endpoint)
        port: Database port (default: 3306)

    Returns:
        JSON string with status and performance_schema value
    """
    logger.info(f"🔧 [{INVOCATION_ID}] TOOL check_performance_schema STARTED - host={host}")
    start_time = time.time()

    connection = None
    try:
        connection = _connect(host, port)

        with connection.cursor() as cursor:
            cursor.execute("SHOW VARIABLES LIKE 'performance_schema'")
            result = cursor.fetchone()

        if result:
            is_enabled = result['Value'].upper() == 'ON'
            elapsed = time.time() - start_time
            logger.info(f"✅ [{INVOCATION_ID}] TOOL check_performance_schema COMPLETED - {elapsed:.2f}s - enabled={is_enabled}")
            return json.dumps({
                "status": "success",
                "performance_schema": result['Value'],
                "is_enabled": is_enabled,
                "message": f"Performance_schema is {'enabled' if is_enabled else 'disabled'}"
            })
        else:
            return json.dumps({
                "status": "error",
                "is_enabled": False,
                "message": "Could not retrieve performance_schema status"
            })

    except pymysql.err.OperationalError as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ [{INVOCATION_ID}] TOOL check_performance_schema DB connection failed - {elapsed:.2f}s - {type(e).__name__}: {e}")
        return json.dumps({
            "status": "error",
            "is_enabled": False,
            "message": "Could not connect to the database (check endpoint, security group, TLS, and credentials)."
        })
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ [{INVOCATION_ID}] TOOL check_performance_schema FAILED - {elapsed:.2f}s - {type(e).__name__}: {e}")
        return json.dumps({
            "status": "error",
            "is_enabled": False,
            "message": f"Failed to check performance_schema: {type(e).__name__}."
        })
    finally:
        if connection is not None:
            connection.close()


@tool
def collect_innodb_status(host: str, port: int = 3306) -> str:
    """
    Collect SHOW ENGINE INNODB STATUS from MySQL instance

    Args:
        host: Database host (RDS endpoint)
        port: Database port (default: 3306)

    Returns:
        Success message with InnoDB status summary
    """
    global _innodb_status

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL collect_innodb_status STARTED - host={host}")
    start_time = time.time()

    connection = None
    try:
        connection = _connect(host, port)

        with connection.cursor() as cursor:
            # Get MySQL version
            cursor.execute("SELECT VERSION() as version")
            version_result = cursor.fetchone()
            mysql_version = version_result['version'] if version_result else 'unknown'

            # Get InnoDB status
            cursor.execute("SHOW ENGINE INNODB STATUS")
            status_result = cursor.fetchone()

        if status_result and 'Status' in status_result:
            _innodb_status = {
                "host": host,
                "version": mysql_version,
                "status": status_result['Status'],
                "collected_at": datetime.now().isoformat()
            }

            # Get status length for summary
            status_length = len(status_result['Status'])

            elapsed = time.time() - start_time
            result_msg = f"✅ Collected InnoDB status from MySQL {mysql_version} ({status_length} chars)"
            logger.info(f"✅ [{INVOCATION_ID}] TOOL collect_innodb_status COMPLETED - {elapsed:.2f}s - {status_length} chars")
            return result_msg
        else:
            return "❌ Failed to get InnoDB status: Empty result"

    except pymysql.err.OperationalError as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ [{INVOCATION_ID}] TOOL collect_innodb_status DB connection failed - {elapsed:.2f}s - {type(e).__name__}: {e}")
        return "❌ Failed to collect InnoDB status: could not connect to the database (check endpoint, security group, TLS, and credentials)."
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ [{INVOCATION_ID}] TOOL collect_innodb_status FAILED - {elapsed:.2f}s - {type(e).__name__}: {e}")
        return f"❌ Failed to collect InnoDB status: {type(e).__name__}."
    finally:
        if connection is not None:
            connection.close()


@tool
def get_innodb_status_data() -> str:
    """
    Get the collected InnoDB status data for LLM analysis

    Returns:
        The full InnoDB status output with metadata
    """
    global _innodb_status

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL get_innodb_status_data STARTED")
    start_time = time.time()

    if not _innodb_status:
        logger.warning(f"⚠️  [{INVOCATION_ID}] TOOL get_innodb_status_data FAILED - No data collected")
        return "❌ InnoDB status has not been collected yet. Run collect_innodb_status first."

    result = f"""
=== InnoDB Status Analysis Data ===
Host: {_innodb_status['host']}
MySQL Version: {_innodb_status['version']}
Collected At: {_innodb_status['collected_at']}

=== SHOW ENGINE INNODB STATUS Output ===
{_innodb_status['status']}
"""

    elapsed = time.time() - start_time
    logger.info(f"✅ [{INVOCATION_ID}] TOOL get_innodb_status_data COMPLETED - {elapsed:.2f}s - {len(result)} chars")
    return result


@tool
def collect_query_risk_data(host: str, port: int = 3306) -> str:
    """
    Collect query optimizer risk data from performance_schema.
    Identifies queries that may behave differently in MySQL 8.4 due to optimizer changes.

    Args:
        host: Database host (RDS endpoint)
        port: Database port (default: 3306)

    Returns:
        Success message with number of risky queries found
    """
    global _query_risk_data

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL collect_query_risk_data STARTED - host={host}")
    start_time = time.time()

    query = """
SELECT
    hist.DIGEST_TEXT AS query_template,
    hist.COUNT_STAR AS total_exec_count,
    ROUND(hist.SUM_TIMER_WAIT / 1000000000000, 2) AS total_exec_sec,
    ROUND(hist.AVG_TIMER_WAIT / 1000000000000, 6) AS avg_exec_sec,
    CONCAT_WS(', ',
        IF(hist.DIGEST_TEXT LIKE '%JOIN%', 'HASH_JOIN', NULL),
        IF(hist.DIGEST_TEXT LIKE '%FROM (%', 'DERIVED_MERGE', NULL),
        IF(hist.DIGEST_TEXT LIKE '%JOIN%' AND hist.DIGEST_TEXT LIKE '%WHERE%', 'CONDITION_PUSHDOWN', NULL),
        IF(hist.DIGEST_TEXT REGEXP 'IN \\\\(|BETWEEN', 'RANGE_OPTIMIZER', NULL),
        IF(hist.DIGEST_TEXT LIKE '%ORDER BY%', 'ORDER_BY_OPT', NULL),
        IF(hist.DIGEST_TEXT REGEXP 'IS NULL|IS NOT NULL', 'NULL_FILTER', NULL),
        IF(hist.DIGEST_TEXT REGEXP 'WHERE .*=' OR hist.DIGEST_TEXT REGEXP 'JOIN .*=', 'AHI_IMPACT', NULL)
    ) AS risk_types,
    ROUND(
        (
            (CASE WHEN hist.DIGEST_TEXT LIKE '%JOIN%' THEN 3 ELSE 0 END) +
            (CASE WHEN hist.DIGEST_TEXT LIKE '%FROM (%' THEN 3 ELSE 0 END) +
            (CASE WHEN hist.DIGEST_TEXT LIKE '%JOIN%' AND hist.DIGEST_TEXT LIKE '%WHERE%' THEN 2 ELSE 0 END) +
            (CASE WHEN hist.DIGEST_TEXT REGEXP 'IN \\\\(|BETWEEN' THEN 2 ELSE 0 END) +
            (CASE WHEN hist.DIGEST_TEXT LIKE '%ORDER BY%' THEN 2 ELSE 0 END) +
            (CASE WHEN hist.DIGEST_TEXT REGEXP 'IS NULL|IS NOT NULL' THEN 1 ELSE 0 END) +
            (CASE WHEN hist.DIGEST_TEXT REGEXP 'WHERE .*=' OR hist.DIGEST_TEXT REGEXP 'JOIN .*=' THEN 2 ELSE 0 END)
        )
        * LOG10(hist.COUNT_STAR + 1)
        * (hist.AVG_TIMER_WAIT / 1000000000000)
    , 2) AS risk_score,
    hist.QUERY_SAMPLE_TEXT AS sample_query
FROM performance_schema.events_statements_summary_by_digest hist
WHERE hist.DIGEST_TEXT IS NOT NULL
  AND hist.DIGEST_TEXT LIKE 'SELECT%'
  AND hist.SCHEMA_NAME IS NOT NULL
  AND hist.SCHEMA_NAME NOT IN ('performance_schema', 'information_schema', 'mysql', 'sys', 'rdsadmin')
ORDER BY
    risk_score DESC,
    total_exec_sec DESC,
    total_exec_count DESC
LIMIT 100
"""

    connection = None
    try:
        connection = _connect(host, port)

        with connection.cursor() as cursor:
            cursor.execute(query)
            results = cursor.fetchall()

        # Filter out queries with no risk types or zero risk score
        risky_queries = [r for r in results if r['risk_types'] and r['risk_score'] and r['risk_score'] > 0]

        _query_risk_data = {
            "host": host,
            "collected_at": datetime.now().isoformat(),
            "total_queries_analyzed": len(results),
            "risky_queries_count": len(risky_queries),
            "queries": risky_queries[:50]  # Top 50 risky queries
        }

        elapsed = time.time() - start_time
        result_msg = f"✅ Query risk analysis complete: {len(risky_queries)} risky queries found out of {len(results)} analyzed"
        logger.info(f"✅ [{INVOCATION_ID}] TOOL collect_query_risk_data COMPLETED - {elapsed:.2f}s - {len(risky_queries)} risky queries")
        return result_msg

    except pymysql.err.OperationalError as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ [{INVOCATION_ID}] TOOL collect_query_risk_data DB connection failed - {elapsed:.2f}s - {type(e).__name__}: {e}")
        return "❌ Failed to collect query risk data: could not connect to the database (check endpoint, security group, TLS, and credentials)."
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ [{INVOCATION_ID}] TOOL collect_query_risk_data FAILED - {elapsed:.2f}s - {type(e).__name__}: {e}")
        return f"❌ Failed to collect query risk data: {type(e).__name__} while querying performance_schema."
    finally:
        if connection is not None:
            connection.close()


@tool
def get_query_risk_data() -> str:
    """
    Get the collected query risk data for LLM analysis

    Returns:
        JSON formatted query risk analysis data
    """
    global _query_risk_data

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL get_query_risk_data STARTED")
    start_time = time.time()

    if not _query_risk_data:
        logger.warning(f"⚠️  [{INVOCATION_ID}] TOOL get_query_risk_data FAILED - No data collected")
        return "❌ Query risk data has not been collected yet. Run collect_query_risk_data first."

    # Format as readable text for LLM
    result = f"""
=== Query Optimizer Risk Analysis Data ===
Host: {_query_risk_data['host']}
Collected At: {_query_risk_data['collected_at']}
Total Queries Analyzed: {_query_risk_data['total_queries_analyzed']}
Risky Queries Found: {_query_risk_data['risky_queries_count']}

=== Top Risky Queries (sorted by risk_score) ===
"""
    for i, q in enumerate(_query_risk_data['queries'][:30], 1):
        result += f"""
--- Query #{i} ---
Risk Score: {q['risk_score']}
Risk Types: {q['risk_types']}
Exec Count: {q['total_exec_count']}
Avg Exec Time: {q['avg_exec_sec']}s
Query Template: {q['query_template'][:200]}{'...' if len(q['query_template'] or '') > 200 else ''}
"""

    elapsed = time.time() - start_time
    logger.info(f"✅ [{INVOCATION_ID}] TOOL get_query_risk_data COMPLETED - {elapsed:.2f}s - {len(result)} chars")
    return result


def build_language_directive(language: str) -> str:
    """Return an output-language instruction block prepended to the agent prompt."""
    if (language or "ko").strip().lower() == "en":
        return (
            "**Output language: English**\n"
            "- Write the ENTIRE report in English: title, all section headings, "
            "table headers, descriptions, and recommendations.\n"
            "- The report structure shown below uses Korean headings only as a "
            "layout guide — translate every Korean label/heading into natural "
            "English while keeping the same structure.\n"
            "- Keep MySQL identifiers, variable names, SQL, and metric names "
            "unchanged.\n"
        )
    return (
        "**출력 언어: 한국어**\n"
        "- 리포트 전체(제목, 모든 섹션 헤더, 표 헤더, 설명, 권고)를 한국어로 작성하세요.\n"
    )


# Timestamp labels the LLM tends to emit (KO + EN), used to strip any
# generation date/time from report bodies. The S3 object name already carries
# a real timestamp, so an LLM-invented date in the body is pure noise.
_TIMESTAMP_LABELS = (
    "생성 일시", "수집 일시", "분석 일시", "작성 일시",
    "Report generated", "Generated at", "Generated on",
    "Collected at", "Analyzed at", "Date generated",
)


def _strip_report_time(markdown: str) -> str:
    """Remove any generation/analysis date line the LLM added to the report.

    The prompt tells the model not to print a date, but LLMs add one anyway (and
    hallucinate the year). We delete it deterministically. Only timestamp-labeled
    text is touched, so data values are never altered.
    """
    labels = "|".join(re.escape(x) for x in _TIMESTAMP_LABELS)
    markdown = re.sub(rf"\*?\*?(?:{labels})\*?\*?\s*[:：][^|\n]*\|\s*", "", markdown)
    markdown = re.sub(rf"\s*\|\s*\*?\*?(?:{labels})\*?\*?\s*[:：][^|\n]*", "", markdown)
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
def save_report(markdown_content: str, s3_bucket: str = "rds-mysql-upgrade-reports",
                s3_key_prefix: str = "upgrade-readiness") -> str:
    """
    Save the markdown analysis report to S3 and generate presigned URL

    Args:
        markdown_content: The markdown content to save
        s3_bucket: S3 bucket name
        s3_key_prefix: S3 key prefix

    Returns:
        JSON string with s3_url and presigned_url
    """
    global _report_urls
    import boto3

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL save_report STARTED - bucket={s3_bucket}, content_length={len(markdown_content)}")
    start_time = time.time()

    try:
        # Strip any generation date the LLM added — the S3 object name already
        # carries a real timestamp, and LLM-written dates are unreliable.
        markdown_content = _strip_report_time(markdown_content)
        # Prepend the human-review disclaimer (deterministic, not LLM-authored).
        markdown_content = _prepend_disclaimer(markdown_content, _report_language)

        s3_client = boto3.client('s3', region_name='us-east-1')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        s3_key = f"{s3_key_prefix}/upgrade_readiness_report_{timestamp}.md"

        s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=markdown_content.encode('utf-8'),
            ContentType='text/markdown'
        )

        s3_url = f"s3://{s3_bucket}/{s3_key}"

        # Generate presigned URL (1 hour expiration)
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': s3_bucket, 'Key': s3_key},
            ExpiresIn=3600
        )

        # Store URLs in global variable for reliable access
        _report_urls["s3_url"] = s3_url
        _report_urls["presigned_url"] = presigned_url

        result = {
            "s3_url": s3_url,
            "presigned_url": presigned_url
        }

        elapsed = time.time() - start_time
        logger.info(f"✅ [{INVOCATION_ID}] TOOL save_report COMPLETED - {elapsed:.2f}s - {s3_url}")
        logger.info(f"📥 [{INVOCATION_ID}] Presigned URL stored: {presigned_url[:80]}...")
        return json.dumps(result)

    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = f"ERROR: {str(e)}"
        logger.error(f"❌ [{INVOCATION_ID}] TOOL save_report FAILED - {elapsed:.2f}s - {str(e)}")
        return error_msg


@app.entrypoint
async def invoke(payload):
    """
    AgentCore entrypoint handler

    Expected payload format:
    {
        "blue_host": "mysql-blue.xxx.rds.amazonaws.com",
        "db_secret_id": "<Secrets Manager secret name or ARN>",
        "db_user": "admin",  # optional, overrides secret username
        "s3_bucket": "rds-mysql-upgrade-reports"  # optional
    }
    """
    invoke_start = time.time()
    logger.info(f"🚀 [{INVOCATION_ID}] ========== INVOKE FUNCTION STARTED ==========")
    logger.info(f"📥 [{INVOCATION_ID}] Received payload keys: {list(payload.keys())}")

    # Extract configuration from payload
    blue_host = payload.get("blue_host")
    db_secret_id = payload.get("db_secret_id")
    db_user = payload.get("db_user")  # optional override of the secret's username
    s3_bucket = payload.get("s3_bucket", "rds-mysql-upgrade-reports")
    language = payload.get("language", "ko")  # "ko" or "en"
    global _report_language
    _report_language = language
    language_directive = build_language_directive(language)

    if not all([blue_host, db_secret_id]):
        return {
            "status": "error",
            "message": "Missing required configuration: blue_host and db_secret_id are required"
        }

    # Resolve credentials from Secrets Manager; the LLM never sees them.
    global _credentials
    try:
        _credentials = _load_db_credentials(db_secret_id, db_user)
    except Exception as e:
        logger.error(f"❌ [{INVOCATION_ID}] Failed to load DB credentials from Secrets Manager: {type(e).__name__}")
        return {
            "status": "error",
            "message": "Failed to load DB credentials from Secrets Manager. Check db_secret_id and the runtime role's secretsmanager:GetSecretValue permission."
        }

    # Build agent message for comprehensive analysis
    agent_message = f"""
{language_directive}
MySQL 8.4 업그레이드 준비 분석 - InnoDB 상태 + 쿼리 옵티마이저 리스크 분석

**작업 순서:**
1. check_performance_schema로 Performance_schema 확인 (host="{blue_host}")
   - 자격증명은 도구 내부에서 자동으로 처리됩니다 (인자로 전달하지 마세요).
   - 비활성화면 "Performance_schema 비활성화" 메시지와 함께 종료

2. InnoDB 상태 분석:
   - collect_innodb_status로 InnoDB 상태 수집
   - get_innodb_status_data로 데이터 조회

3. 쿼리 옵티마이저 리스크 분석:
   - collect_query_risk_data로 리스크 쿼리 수집
   - get_query_risk_data로 데이터 조회

4. save_report로 통합 리포트 저장 (s3_bucket="{s3_bucket}")

**리포트 형식:**
```markdown
# MySQL 8.4 업그레이드 준비 분석 리포트

**분석 대상:** MySQL X.X.XX (Blue)

---

## 섹션 1: InnoDB 상태 분석

### 🔴 조치 필요
| 항목 | 현재 값 | 8.4 변경사항 | 권장 조치 |
|------|---------|--------------|-----------|

### 🟡 확인 필요
| 항목 | 현재 상태 | 비고 |
|------|----------|------|

### 🟢 정상
- 항목: 상태

---

## 섹션 2: 쿼리 옵티마이저 리스크 분석

### 8.4 옵티마이저 변경사항 영향 받는 쿼리

| Risk Score | Risk Types | 실행횟수 | Query (요약) |
|------------|------------|---------|--------------|

### Risk Type 설명
- **HASH_JOIN**: 8.4에서 hash join 동작 변경
- **DERIVED_MERGE**: derived table merge 최적화 변경
- **CONDITION_PUSHDOWN**: condition pushdown 동작 변경
- **RANGE_OPTIMIZER**: range optimizer 개선
- **ORDER_BY_OPT**: ORDER BY 최적화 변경
- **NULL_FILTER**: NULL 필터링 최적화
- **AHI_IMPACT**: Adaptive Hash Index 영향

### 권장 조치
(리스크 쿼리에 대한 권장 조치 요약)
```

**중요:**
- 출력 언어는 위의 "출력 언어" 지시를 따를 것
- **리포트에 생성/분석 일시·날짜를 넣지 말 것 (예: "분석 일시", "Report generated" 등 금지).**
- 각 섹션은 핵심만 간결하게
- 결론, 참고자료 생략
- 리스크 쿼리는 상위 10개만 테이블에 포함
"""

    # Create agent with tools
    logger.info(f"🤖 [{INVOCATION_ID}] ========== CREATING agent ==========")

    # Configure boto client with longer timeouts for streaming
    boto_config = BotocoreConfig(
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=60,
        read_timeout=300  # 5 minutes for long streaming responses
    )

    bedrock_model = BedrockModel(
        model_id=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        region_name="us-east-1",
        boto_client_config=boto_config
    )

    agent = Agent(
        tools=[
            check_performance_schema,
            collect_innodb_status,
            get_innodb_status_data,
            collect_query_risk_data,
            get_query_risk_data,
            save_report
        ],
        model=bedrock_model
    )

    # Run the agent
    logger.info(f"🤖 [{INVOCATION_ID}] ========== CALLING agent.invoke_async() ==========")
    agent_start = time.time()

    result = await agent.invoke_async(agent_message)

    agent_elapsed = time.time() - agent_start
    logger.info(f"✅ [{INVOCATION_ID}] ========== agent() RETURNED - {agent_elapsed:.2f}s ==========")

    # Log token usage for this sub-agent
    log_token_usage(result, AGENT_NAME)

    # Get URLs from global storage
    global _report_urls, _innodb_status, _query_risk_data
    s3_url = _report_urls.get("s3_url")
    presigned_url = _report_urls.get("presigned_url")

    # Build response
    response = {
        "result": result.message,
        "status": "success",
        "blue_host": blue_host
    }

    if presigned_url:
        response["presigned_url"] = presigned_url
        response["s3_url"] = s3_url

    if _innodb_status:
        response["mysql_version"] = _innodb_status.get("version", "unknown")

    if _query_risk_data:
        response["risky_queries_count"] = _query_risk_data.get("risky_queries_count", 0)

    invoke_elapsed = time.time() - invoke_start
    logger.info(f"🏁 [{INVOCATION_ID}] ========== INVOKE FUNCTION COMPLETED - {invoke_elapsed:.2f}s ==========")

    return response


if __name__ == "__main__":
    app.run()
