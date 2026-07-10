"""
MySQL Blue-Green Variables Comparison Agent
Compares SHOW VARIABLES between Blue (8.0.x) and Green (8.4.x) instances
LLM analyzes the differences and generates a markdown report
"""

import os
import re
import json
import pymysql
import logging
import time
import uuid
import boto3
from datetime import datetime
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Agent name for metrics tracking
AGENT_NAME = "rds_mysql_variables_compare"

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


# Global storage for variables and report URLs
_blue_variables = None
_green_variables = None
_report_urls = {"s3_url": None, "presigned_url": None}
# DB connection settings, populated by invoke() before the LLM runs — never
# passed through the LLM prompt. Blue and Green are clones of each other, so a
# single Secrets Manager secret authenticates both. The password is resolved
# from Secrets Manager (see _load_db_credentials), never via the payload.
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
def collect_variables(host: str, port: int = 3306,
                       instance_type: str = "blue") -> str:
    """
    Collect MySQL variables from a database instance

    Args:
        host: Database host (RDS endpoint)
        port: Database port (default: 3306)
        instance_type: "blue" or "green" to identify which instance

    Returns:
        Success message with version and count
    """
    global _blue_variables, _green_variables

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL collect_variables STARTED - instance_type={instance_type}, host={host}")
    start_time = time.time()

    if not _credentials:
        return "❌ No credentials loaded"

    connection = None
    try:
        connection = _connect(host, port)

        with connection.cursor() as cursor:
            cursor.execute("SHOW VARIABLES")
            results = cursor.fetchall()

        # Convert to dictionary
        variables_dict = {
            row['Variable_name']: row['Value']
            for row in results
        }

        version = variables_dict.get('version', 'unknown')

        # Store in appropriate global variable
        if instance_type.lower() == "blue":
            _blue_variables = variables_dict
        elif instance_type.lower() == "green":
            _green_variables = variables_dict

        elapsed = time.time() - start_time
        result_msg = f"✅ Collected {len(variables_dict)} variables from {instance_type.upper()} instance (MySQL {version})"
        logger.info(f"✅ [{INVOCATION_ID}] TOOL collect_variables COMPLETED - {elapsed:.2f}s - {len(variables_dict)} variables")
        return result_msg

    except pymysql.err.OperationalError as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ [{INVOCATION_ID}] TOOL collect_variables DB connection failed ({instance_type.upper()}) - {elapsed:.2f}s - {type(e).__name__}: {e}")
        return f"❌ Failed to collect variables from {instance_type.upper()}: could not connect to the database (check endpoint, security group, TLS, and credentials)."
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ [{INVOCATION_ID}] TOOL collect_variables FAILED ({instance_type.upper()}) - {elapsed:.2f}s - {type(e).__name__}: {e}")
        return f"❌ Failed to collect variables from {instance_type.upper()}: {type(e).__name__}."
    finally:
        if connection is not None:
            connection.close()


@tool
def get_variables_summary() -> str:
    """
    Get a summary of collected variables for LLM to analyze

    Returns:
        Formatted text with Blue and Green variables for comparison
    """
    global _blue_variables, _green_variables

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL get_variables_summary STARTED")
    start_time = time.time()

    if not _blue_variables or not _green_variables:
        logger.warning(f"⚠️  [{INVOCATION_ID}] TOOL get_variables_summary FAILED - Missing variables")
        return "❌ Both Blue and Green variables must be collected first"

    blue_version = _blue_variables.get('version', 'unknown')
    green_version = _green_variables.get('version', 'unknown')

    # Format Blue variables
    blue_text = f"BLUE INSTANCE (MySQL {blue_version}) - {len(_blue_variables)} variables:\n"
    for var, val in sorted(_blue_variables.items()):
        blue_text += f"{var}={val}\n"

    # Format Green variables
    green_text = f"\nGREEN INSTANCE (MySQL {green_version}) - {len(_green_variables)} variables:\n"
    for var, val in sorted(_green_variables.items()):
        green_text += f"{var}={val}\n"

    elapsed = time.time() - start_time
    result = blue_text + green_text
    logger.info(f"✅ [{INVOCATION_ID}] TOOL get_variables_summary COMPLETED - {elapsed:.2f}s - {len(result)} chars")
    return result


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
    """Remove any generation/collection date line the LLM added to the report.

    The prompt tells the model not to print a date, but LLMs add one anyway (and
    hallucinate the year). We delete it deterministically:
      - a standalone bold line like `**생성 일시:** ...`  → drop the whole line
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
def save_report(markdown_content: str, s3_bucket: str = "rds-mysql-upgrade-reports",
                s3_key_prefix: str = "blue-green-comparison") -> str:
    """
    Save the markdown comparison report to S3 and generate presigned URL

    Args:
        markdown_content: The markdown content to save
        s3_bucket: S3 bucket name
        s3_key_prefix: S3 key prefix

    Returns:
        JSON string with s3_url and presigned_url
    """
    global _report_urls
    import boto3
    import json
    from datetime import datetime

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
        s3_key = f"{s3_key_prefix}/variables_comparison_{timestamp}.md"

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


# Agent will be created per-invocation to avoid concurrency issues
# (moved inside invoke() function)


@app.entrypoint
async def invoke(payload):
    """
    AgentCore entrypoint handler

    Expected payload format:
    {
        "blue_host": "mysql-blue.xxx.rds.amazonaws.com",
        "green_host": "mysql-green.xxx.rds.amazonaws.com",
        "db_secret_id": "<Secrets Manager secret name or ARN>",
        "db_user": "admin",  # optional, overrides secret username
        "s3_bucket": "rds-mysql-upgrade-reports",  # optional, for report storage
        "report_mode": "full"  # optional: "full" (default) or "custom_only"
    }
    """
    invoke_start = time.time()
    logger.info(f"🚀 [{INVOCATION_ID}] ========== INVOKE FUNCTION STARTED ==========")
    logger.info(f"📥 [{INVOCATION_ID}] Received payload keys: {list(payload.keys())}")

    # Extract configuration from payload
    blue_host = payload.get("blue_host")
    green_host = payload.get("green_host")
    db_secret_id = payload.get("db_secret_id")
    db_user = payload.get("db_user")  # optional override of the secret's username
    s3_bucket = payload.get("s3_bucket", "rds-mysql-upgrade-reports")
    report_mode = payload.get("report_mode", "full")  # "full" or "custom_only"
    language = payload.get("language", "ko")  # "ko" or "en"
    global _report_language
    _report_language = language
    language_directive = build_language_directive(language)

    if not all([blue_host, green_host, db_secret_id]):
        return {
            "error": "Missing required configuration",
            "message": "blue_host, green_host, and db_secret_id are required"
        }

    # Resolve credentials from Secrets Manager; the LLM never sees them.
    # Blue and Green are clones, so one secret authenticates both.
    global _credentials
    try:
        _credentials = _load_db_credentials(db_secret_id, db_user)
    except Exception as e:
        logger.error(f"❌ [{INVOCATION_ID}] Failed to load DB credentials from Secrets Manager: {type(e).__name__}")
        return {
            "error": "Secrets Manager error",
            "message": "Failed to load DB credentials from Secrets Manager. Check db_secret_id and the runtime role's secretsmanager:GetSecretValue permission."
        }

    # Build filter instruction based on report_mode
    filter_instruction = ""
    if report_mode == "custom_only":
        filter_instruction = """
**중요: 리포트 모드 = custom_only**
- "변경된 변수" 섹션: 양쪽 모두 (default)인 변수는 제외
- 즉, Blue 또는 Green 중 적어도 한쪽이 (default)가 아닌 변수만 표시
- 사용자가 커스터마이즈한 변수에만 집중하기 위함
"""
    else:
        filter_instruction = """
**리포트 모드 = full (전체 표시)**
- 모든 변경된 변수를 표시 (양쪽 모두 default인 경우 포함)
"""

    # Build agent message
    agent_message = f"""
{language_directive}
Blue/Green MySQL 변수 비교 후 간단한 마크다운 리포트 작성.

**작업:**
1. collect_variables로 Blue(instance_type="blue"), Green(instance_type="green") 수집
   - Blue: host="{blue_host}"
   - Green: host="{green_host}"
   - 자격증명은 도구 내부에서 자동으로 처리됩니다 (인자로 전달하지 마세요).
2. get_variables_summary로 데이터 조회
3. 분석 후 save_report로 저장 (s3_bucket="{s3_bucket}")
   - save_report는 JSON 형태로 s3_url과 presigned_url을 반환함
   - 최종 응답에 presigned_url을 포함해서 사용자에게 전달

{filter_instruction}

**리포트 작성 규칙:**
- 출력 언어는 위의 "출력 언어" 지시를 따를 것
- **리포트에 생성/작성 일시·날짜를 넣지 말 것 (예: "생성 일시", "Report generated" 등 금지). 날짜 줄을 추가하지 마세요.**
- 변수 표는 모두 나열 (값이 비어있으면 그냥 빈칸으로)
- **변경된 변수 표: Blue와 Green 모두 해당 MySQL 버전의 디폴트 값이면 "(default)" 표시**
- **설명은 간결하게 1~2줄로, 핵심만 작성 (timeout 방지)**

**리포트 구조:**
```markdown
# MySQL 변수 비교 리포트

**Blue 인스턴스:** MySQL X.X.XX (N개 변수)
**Green 인스턴스:** MySQL Y.Y.YY (M개 변수)

## 요약
- 삭제된 변수: X개
- 추가된 변수: Y개
- 변경된 변수: Z개

## 1. 삭제된 변수

Blue (8.0.x)에는 있지만 Green (8.4.x)에서 제거된 변수 목록입니다.
애플리케이션에서 이 변수들을 사용하는지 확인이 필요합니다.

| 변수명 | Blue 값 | 설명 |
|--------|---------|------|
| 변수1 | 값1 | 간단한 설명 (1~2줄) |
| ... | ... | ... |

## 2. 추가된 변수

Green (8.4.x)에 새로 추가된 변수 목록입니다.
신규 기능이나 성능 개선 기회를 검토하세요.

| 변수명 | Green 값 | 설명 |
|--------|----------|------|
| 변수1 | 값1 | 간단한 설명 (1~2줄) |
| ... | ... | ... |

## 3. 변경된 변수

양쪽에 모두 존재하지만 값이 다른 변수 목록입니다.
- Blue 값이 MySQL 8.0.45 디폴트면 "(default)" 표시
- Green 값이 MySQL 8.4.8 디폴트면 "(default)" 표시

| 변수명 | Blue 값 | Green 값 | 설명 |
|--------|---------|----------|------|
| 변수1 | 값1 (default) | 값2 (default) | 간단한 설명 (1~2줄) |
| 변수2 | 값A | 값B (default) | 간단한 설명 (1~2줄) |
| ... | ... | ... | ... |

## 4. 마이그레이션 시 주의사항

다음 항목들을 우선적으로 검토하세요:

- **삭제된 변수**: 애플리케이션 코드에서 참조하는지 확인
- **변경된 변수**: 특히 replication, character set, transaction isolation 관련 변수는 테스트 필수
- **추가된 변수**: 새 기능 활용으로 성능/보안 개선 기회 검토
```
"""

    # Create agent with tools (per-invocation to avoid concurrency issues)
    logger.info(f"🤖 [{INVOCATION_ID}] ========== CREATING agent ==========")
    agent = Agent(
        tools=[collect_variables, get_variables_summary, save_report],
        model=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    )

    # Run the agent (ASYNC)
    logger.info(f"🤖 [{INVOCATION_ID}] ========== CALLING agent.invoke_async() ==========")
    agent_start = time.time()

    result = await agent.invoke_async(agent_message)

    agent_elapsed = time.time() - agent_start
    logger.info(f"✅ [{INVOCATION_ID}] ========== agent() RETURNED - {agent_elapsed:.2f}s ==========")

    # Log token usage for this sub-agent
    log_token_usage(result, AGENT_NAME)
    logger.info(f"📤 [{INVOCATION_ID}] Result type: {type(result)}, has message: {hasattr(result, 'message')}")

    # Extract message text (result.message is a dict with 'content' key)
    message_text = ""
    if isinstance(result.message, dict) and 'content' in result.message:
        for content in result.message['content']:
            if 'text' in content:
                message_text += content['text']
    elif isinstance(result.message, str):
        message_text = result.message

    # Get URLs from global storage (set by save_report tool)
    global _report_urls
    s3_url = _report_urls.get("s3_url")
    presigned_url = _report_urls.get("presigned_url")

    logger.info(f"📥 [{INVOCATION_ID}] Report URLs - s3: {bool(s3_url)}, presigned: {bool(presigned_url)}")

    # Build response
    response = {
        "result": result.message,
        "status": "success",
        "blue_host": blue_host,
        "green_host": green_host
    }

    # Always include presigned URL from global storage
    if presigned_url:
        response["presigned_url"] = presigned_url
        response["s3_url"] = s3_url
        logger.info(f"✅ [{INVOCATION_ID}] Presigned URL included in response")

    # Include variable counts if available
    global _blue_variables, _green_variables
    if _blue_variables and _green_variables:
        response["blue_version"] = _blue_variables.get('version', 'unknown')
        response["green_version"] = _green_variables.get('version', 'unknown')
        response["blue_variable_count"] = len(_blue_variables)
        response["green_variable_count"] = len(_green_variables)

    invoke_elapsed = time.time() - invoke_start
    logger.info(f"🏁 [{INVOCATION_ID}] ========== INVOKE FUNCTION COMPLETED - {invoke_elapsed:.2f}s ==========")
    logger.info(f"📤 [{INVOCATION_ID}] Response keys: {list(response.keys())}")

    return response


if __name__ == "__main__":
    app.run()
