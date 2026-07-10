"""
MySQL Blue-Green Upgrade Analysis Orchestrator Agent
Coordinates multiple analysis agents in sequence:
1. Check Blue-Green deployment status
2. Analyze error logs (Error Log Analyzer Agent)
3. Compare variables (Variables Compare Agent)
4. Analyze upgrade readiness - InnoDB status + Query optimizer risk (Upgrade Readiness Analyzer Agent)
5. Generate final summary
"""

import os
import logging
import time
import uuid
import json
import re
from datetime import datetime
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
import boto3
import pymysql
from botocore.exceptions import ClientError
from botocore.config import Config

# Agent name for metrics tracking
AGENT_NAME = "rds_mysql_upgrade_orchestrator"

# Configure boto3 client with extended timeouts for sub-agent calls
AGENT_CLIENT_CONFIG = Config(
    retries={'max_attempts': 1, 'mode': 'standard'},
    read_timeout=300,      # 5 minutes (sub-agents can take 1-2 min each)
    connect_timeout=60
)

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Generate unique invocation ID for tracking
INVOCATION_ID = str(uuid.uuid4())[:8]
logger.info(f"🔵 ORCHESTRATOR MODULE LOADED - Invocation ID: {INVOCATION_ID}")


# TLS: RDS/Aurora global CA bundle baked into the image by the Dockerfile.
RDS_CA_BUNDLE = "/app/rds-ca-bundle.pem"
# Connection timeouts (seconds): fail fast on bad endpoint / SG / provisioning.
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 30


def _load_db_credentials(secret_id: str, db_user: str | None = None) -> dict:
    """Resolve DB credentials from Secrets Manager.

    Expects an RDS-style secret ({"username", "password"} JSON). Returns
    {"user", "password"}. db_user, if given, overrides the secret's username.
    The orchestrator uses this only for its own Blue-Green reachability check;
    sub-agents receive the secret id (not the password) and resolve it themselves.
    """
    region = os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("secretsmanager", region_name=region)
    raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
    data = json.loads(raw)
    return {
        "user": db_user or data.get("username", "admin"),
        "password": data["password"],
    }


# Supported report languages. Sub-agents receive this verbatim in their payload.
SUPPORTED_LANGUAGES = ("ko", "en")


def normalize_language(language: str) -> str:
    """Coerce an arbitrary language value into a supported code (default 'ko')."""
    lang = (language or "ko").strip().lower()
    return lang if lang in SUPPORTED_LANGUAGES else "ko"


# Localized display names for the report download buttons in the UI.
REPORT_NAMES = {
    "error_log": {"ko": "에러 로그 분석 리포트", "en": "Error Log Analysis Report"},
    "variables": {"ko": "변수 비교 리포트", "en": "Variables Comparison Report"},
    "upgrade_readiness": {
        "ko": "업그레이드 준비 분석 리포트",
        "en": "Upgrade Readiness Report",
    },
}


def report_name(report_type: str, language: str) -> str:
    """Return the localized download-button label for a report type."""
    names = REPORT_NAMES.get(report_type, {})
    return names.get(language) or names.get("ko") or report_type


# Progress/error messages streamed to the UI, keyed by message id then language.
# {placeholders} are filled by m() via str.format. These surface in the UI's
# step list and detail log, so they follow the same language as the reports.
MESSAGES = {
    "s1_start":   {"ko": "Blue-Green 배포 상태 확인 중...",
                   "en": "Checking Blue-Green deployment status..."},
    "s1_connect": {"ko": "Blue 인스턴스 연결 중... ({host})",
                   "en": "Connecting to the Blue instance... ({host})"},
    "s1_not_ready": {"ko": "Blue-Green 배포가 준비되지 않았습니다",
                     "en": "The Blue-Green deployment is not ready"},
    "s2_start":   {"ko": "에러 로그 분석 에이전트 실행 중...",
                   "en": "Running the Error Log Analyzer agent..."},
    "s2_loggroup": {"ko": "CloudWatch 로그 그룹 조회 중... ({group})",
                    "en": "Looking up the CloudWatch log group... ({group})"},
    "s2_invoke":  {"ko": "에러 로그 분석 에이전트 호출 중... (최대 2분 소요)",
                   "en": "Invoking the Error Log Analyzer agent... (up to 2 min)"},
    "s2_done":    {"ko": "에러 로그 분석 완료, 리포트 생성됨",
                   "en": "Error log analysis complete, report generated"},
    "s3_start":   {"ko": "변수 비교 에이전트 실행 중...",
                   "en": "Running the Variables Compare agent..."},
    "s3_prep":    {"ko": "Blue/Green MySQL 변수 수집 준비 중...",
                   "en": "Preparing to collect Blue/Green MySQL variables..."},
    "s3_invoke":  {"ko": "변수 비교 에이전트 호출 중... (최대 2분 소요)",
                   "en": "Invoking the Variables Compare agent... (up to 2 min)"},
    "s3_done":    {"ko": "변수 비교 완료: Blue {blue}개, Green {green}개",
                   "en": "Variables compared: Blue {blue}, Green {green}"},
    "s4_start":   {"ko": "업그레이드 준비 분석 에이전트 실행 중 (InnoDB 상태 + 쿼리 리스크)...",
                   "en": "Running the Upgrade Readiness agent (InnoDB status + query risk)..."},
    "s4_collect": {"ko": "Blue 인스턴스 분석 데이터 수집 중... ({host})",
                   "en": "Collecting analysis data from the Blue instance... ({host})"},
    "s4_invoke":  {"ko": "업그레이드 준비 분석 에이전트 호출 중... (최대 3분 소요)",
                   "en": "Invoking the Upgrade Readiness agent... (up to 3 min)"},
    "s4_done":    {"ko": "분석 완료: MySQL {version}, 리스크 쿼리 {count}개",
                   "en": "Analysis complete: MySQL {version}, {count} risky queries"},
    "s5_start":   {"ko": "분석 결과 요약 생성 중...",
                   "en": "Generating the analysis summary..."},
    "s5_init":    {"ko": "LLM 모델 초기화 중... (Claude Sonnet)",
                   "en": "Initializing the LLM model... (Claude Sonnet)"},
    "s5_gen":     {"ko": "요약 텍스트 생성 시작...",
                   "en": "Starting summary text generation..."},
    "missing_fields": {
        "ko": ("필수 필드가 누락되었습니다: blue_host, green_host, db_secret_id, "
               "green_log_group, error_log_analyzer_arn, variables_compare_arn, "
               "upgrade_readiness_analyzer_arn"),
        "en": ("Required fields are missing: blue_host, green_host, db_secret_id, "
               "green_log_group, error_log_analyzer_arn, variables_compare_arn, "
               "upgrade_readiness_analyzer_arn"),
    },
    "subagent_failed": {"ko": "❌ {agent} 실행 실패: {error}",
                        "en": "❌ {agent} failed: {error}"},
}


def m(key: str, language: str, **kwargs) -> str:
    """Look up a streamed UI message for the given language and fill placeholders."""
    entry = MESSAGES.get(key, {})
    template = entry.get(language) or entry.get("ko") or key
    return template.format(**kwargs) if kwargs else template


# Initialize AgentCore App
app = BedrockAgentCoreApp()


def log_token_usage(result, agent_name: str = AGENT_NAME):
    """
    Extract and log token usage from Strands Agent result.
    Emits CloudWatch metrics for token tracking across all agents.
    """
    try:
        # Extract usage from result - handle streaming result
        usage = getattr(result, 'usage', None)
        if not usage:
            # Try to get from metrics if available
            metrics = getattr(result, 'metrics', None)
            if metrics:
                usage = getattr(metrics, 'usage', None)

        if not usage:
            logger.warning(f"📊 [{INVOCATION_ID}] No usage data in result")
            return

        # Handle both object and dict formats
        if isinstance(usage, dict):
            input_tokens = usage.get('input_tokens', 0)
            output_tokens = usage.get('output_tokens', 0)
        else:
            input_tokens = getattr(usage, 'input_tokens', 0)
            output_tokens = getattr(usage, 'output_tokens', 0)

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


@tool
def check_blue_green_deployment(blue_host: str, green_host: str, password: str,
                                 username: str = "admin") -> str:
    """
    Check if Blue-Green deployment is ready by testing database connections

    Args:
        blue_host: Blue instance hostname
        green_host: Green instance hostname
        password: Database password
        username: Database username (default: admin)

    Returns:
        JSON string with status: "ready", "not_deployed", or "error"
    """
    logger.info(f"🔧 [{INVOCATION_ID}] TOOL check_blue_green_deployment STARTED")
    start_time = time.time()

    result = {
        "status": "unknown",
        "blue_status": "unknown",
        "green_status": "unknown",
        "blue_version": None,
        "green_version": None,
        "message": ""
    }

    # TLS + timeout-bounded connection (reused for both instances).
    def _check_connect(host):
        return pymysql.connect(
            host=host,
            port=3306,
            user=username,
            password=password,
            connect_timeout=_CONNECT_TIMEOUT,
            read_timeout=_READ_TIMEOUT,
            ssl_ca=RDS_CA_BUNDLE,
            ssl_verify_cert=True,
        )

    # Test Blue instance
    blue_conn = None
    try:
        blue_conn = _check_connect(blue_host)
        with blue_conn.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            blue_version = cursor.fetchone()[0]
            result["blue_version"] = blue_version
            result["blue_status"] = "connected"
        logger.info(f"✅ Blue instance connected: {blue_version}")
    except Exception as e:
        result["blue_status"] = "failed"
        result["message"] += f"Blue connection failed: {type(e).__name__}. "
        logger.error(f"❌ Blue instance connection failed: {type(e).__name__}: {e}")
    finally:
        if blue_conn is not None:
            blue_conn.close()

    # Test Green instance
    green_conn = None
    try:
        green_conn = _check_connect(green_host)
        with green_conn.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            green_version = cursor.fetchone()[0]
            result["green_version"] = green_version
            result["green_status"] = "connected"
        logger.info(f"✅ Green instance connected: {green_version}")
    except Exception as e:
        result["green_status"] = "failed"
        result["message"] += f"Green connection failed: {type(e).__name__}. "
        logger.error(f"❌ Green instance connection failed: {type(e).__name__}: {e}")
    finally:
        if green_conn is not None:
            green_conn.close()

    # Determine overall status
    if result["blue_status"] == "connected" and result["green_status"] == "connected":
        result["status"] = "ready"
        result["message"] = f"Blue-Green deployment ready. Blue: {blue_version}, Green: {green_version}"
    elif result["blue_status"] == "failed" and result["green_status"] == "failed":
        result["status"] = "not_deployed"
        result["message"] = "Blue-Green deployment not ready. Cannot connect to both instances. " + result["message"]
    else:
        result["status"] = "error"
        if result["blue_status"] == "failed":
            result["message"] = "Cannot connect to Blue instance. " + result["message"]
        elif result["green_status"] == "failed":
            result["message"] = "Cannot connect to Green instance. " + result["message"]

    elapsed = time.time() - start_time
    logger.info(f"✅ [{INVOCATION_ID}] TOOL check_blue_green_deployment COMPLETED - {elapsed:.2f}s")

    return json.dumps(result, ensure_ascii=False)


@tool
def run_error_log_analyzer(log_group_name: str, agent_runtime_arn: str,
                           s3_bucket: str = "rds-mysql-upgrade-reports",
                           region: str = "us-east-1",
                           language: str = "ko") -> str:
    """
    Run Error Log Analyzer Agent to analyze MySQL error logs

    Args:
        log_group_name: CloudWatch Logs group name (e.g., /aws/rds/instance/mysql-green/error)
        agent_runtime_arn: Error Log Analyzer agent ARN
        s3_bucket: S3 bucket for report storage
        region: AWS region

    Returns:
        JSON string with presigned_url and summary
    """
    logger.info(f"🔧 [{INVOCATION_ID}] TOOL run_error_log_analyzer STARTED")
    start_time = time.time()

    try:
        # Use extended timeout config for sub-agent calls
        agentcore = boto3.client('bedrock-agentcore', region_name=region, config=AGENT_CLIENT_CONFIG)

        payload = {
            "log_group_name": log_group_name,
            "log_stream_name": None,  # Auto-detect
            "hours_ago": 24,
            "max_events": 1000,
            "region": region,
            "s3_bucket": s3_bucket,
            "language": language
        }

        # Generate session ID (minimum 33 characters required)
        session_id = f"orch-errorlog-{int(time.time())}-{INVOCATION_ID}"

        logger.info(f"📤 Invoking Error Log Analyzer agent with 5min timeout...")
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=agent_runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode()
        )

        response_body = response['response'].read().decode('utf-8')
        logger.info(f"📥 [{INVOCATION_ID}] Error Log Analyzer raw response: {response_body[:500]}...")
        response_json = json.loads(response_body)

        # Extract presigned_url from various possible locations
        presigned_url = response_json.get("presigned_url", "")
        if not presigned_url and "result" in response_json:
            # Try to find it in nested result
            result_data = response_json.get("result", {})
            if isinstance(result_data, dict):
                presigned_url = result_data.get("presigned_url", "")

        result = {
            "status": "success",
            "presigned_url": presigned_url,
            "log_group": log_group_name,
            "hours_analyzed": 24,
            "log_events": response_json.get("log_lines", 0)
        }

        elapsed = time.time() - start_time
        logger.info(f"✅ [{INVOCATION_ID}] TOOL run_error_log_analyzer COMPLETED - {elapsed:.2f}s - presigned_url found: {bool(presigned_url)}")

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = m("subagent_failed", language, agent="Error Log Analyzer", error=str(e))
        logger.error(f"❌ [{INVOCATION_ID}] TOOL run_error_log_analyzer FAILED - {elapsed:.2f}s - {str(e)}")

        return json.dumps({
            "status": "error",
            "message": error_msg
        }, ensure_ascii=False)


@tool
def run_variables_compare(blue_host: str, green_host: str, db_secret_id: str,
                          agent_runtime_arn: str,
                          username: str = "admin",
                          s3_bucket: str = "rds-mysql-upgrade-reports",
                          region: str = "us-east-1",
                          language: str = "ko") -> str:
    """
    Run Variables Compare Agent to compare MySQL variables

    Args:
        blue_host: Blue instance hostname
        green_host: Green instance hostname
        db_secret_id: Secrets Manager secret name or ARN
        agent_runtime_arn: Variables Compare agent ARN
        username: Database username
        s3_bucket: S3 bucket for report storage
        region: AWS region

    Returns:
        JSON string with presigned_url and summary
    """
    logger.info(f"🔧 [{INVOCATION_ID}] TOOL run_variables_compare STARTED")
    start_time = time.time()

    try:
        # Use extended timeout config for sub-agent calls
        agentcore = boto3.client('bedrock-agentcore', region_name=region, config=AGENT_CLIENT_CONFIG)

        payload = {
            "blue_host": blue_host,
            "green_host": green_host,
            "db_secret_id": db_secret_id,
            "db_user": username,
            "s3_bucket": s3_bucket,
            "report_mode": "full",
            "language": language
        }

        # Generate session ID (minimum 33 characters required)
        session_id = f"orch-variables-{int(time.time())}-{INVOCATION_ID}"

        logger.info(f"📤 Invoking Variables Compare agent with 5min timeout...")
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=agent_runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode()
        )

        response_body = response['response'].read().decode('utf-8')
        logger.info(f"📥 [{INVOCATION_ID}] Variables Compare raw response: {response_body[:500]}...")
        response_json = json.loads(response_body)

        # Extract presigned_url from various possible locations
        presigned_url = response_json.get("presigned_url", "")
        if not presigned_url and "result" in response_json:
            result_data = response_json.get("result", {})
            if isinstance(result_data, dict):
                presigned_url = result_data.get("presigned_url", "")

        result = {
            "status": "success",
            "presigned_url": presigned_url,
            "blue_version": response_json.get("blue_version", ""),
            "green_version": response_json.get("green_version", ""),
            "blue_variable_count": response_json.get("blue_variable_count", 0),
            "green_variable_count": response_json.get("green_variable_count", 0)
        }

        elapsed = time.time() - start_time
        logger.info(f"✅ [{INVOCATION_ID}] TOOL run_variables_compare COMPLETED - {elapsed:.2f}s - presigned_url found: {bool(presigned_url)}")

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = m("subagent_failed", language, agent="Variables Compare", error=str(e))
        logger.error(f"❌ [{INVOCATION_ID}] TOOL run_variables_compare FAILED - {elapsed:.2f}s - {str(e)}")

        return json.dumps({
            "status": "error",
            "message": error_msg
        }, ensure_ascii=False)


@tool
def run_upgrade_readiness_analyzer(blue_host: str, db_secret_id: str,
                                    agent_runtime_arn: str,
                                    username: str = "admin",
                                    s3_bucket: str = "rds-mysql-upgrade-reports",
                                    region: str = "us-east-1",
                                    language: str = "ko") -> str:
    """
    Run Upgrade Readiness Analyzer Agent to analyze InnoDB status and query optimizer risks

    This agent performs:
    1. InnoDB Status Analysis - SHOW ENGINE INNODB STATUS
    2. Query Optimizer Risk Analysis - performance_schema queries

    Args:
        blue_host: Blue instance hostname
        db_secret_id: Secrets Manager secret name or ARN
        agent_runtime_arn: Upgrade Readiness Analyzer agent ARN
        username: Database username
        s3_bucket: S3 bucket for report storage
        region: AWS region

    Returns:
        JSON string with presigned_url and analysis summary
    """
    logger.info(f"🔧 [{INVOCATION_ID}] TOOL run_upgrade_readiness_analyzer STARTED")
    start_time = time.time()

    try:
        # Use extended timeout config for sub-agent calls
        agentcore = boto3.client('bedrock-agentcore', region_name=region, config=AGENT_CLIENT_CONFIG)

        payload = {
            "blue_host": blue_host,
            "db_secret_id": db_secret_id,
            "db_user": username,
            "s3_bucket": s3_bucket,
            "language": language
        }

        # Generate session ID (minimum 33 characters required)
        session_id = f"orch-upgrade-readiness-{int(time.time())}-{INVOCATION_ID}"

        logger.info(f"📤 Invoking Upgrade Readiness Analyzer agent with 5min timeout...")
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=agent_runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode()
        )

        response_body = response['response'].read().decode('utf-8')
        logger.info(f"📥 [{INVOCATION_ID}] Upgrade Readiness Analyzer raw response: {response_body[:500]}...")
        response_json = json.loads(response_body)

        # Extract presigned_url
        presigned_url = response_json.get("presigned_url", "")
        if not presigned_url and "result" in response_json:
            result_data = response_json.get("result", {})
            if isinstance(result_data, dict):
                presigned_url = result_data.get("presigned_url", "")

        # Extract summary from agent message
        summary = ""
        if "result" in response_json:
            result_data = response_json.get("result", {})
            if isinstance(result_data, dict) and "content" in result_data:
                for content in result_data.get("content", []):
                    if "text" in content:
                        summary = content["text"][:500]  # First 500 chars
                        break

        result = {
            "status": "success",
            "presigned_url": presigned_url,
            "mysql_version": response_json.get("mysql_version", ""),
            "risky_queries_count": response_json.get("risky_queries_count", 0),
            "blue_host": blue_host,
            "summary": summary
        }

        elapsed = time.time() - start_time
        logger.info(f"✅ [{INVOCATION_ID}] TOOL run_upgrade_readiness_analyzer COMPLETED - {elapsed:.2f}s - presigned_url found: {bool(presigned_url)}")

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = m("subagent_failed", language, agent="Upgrade Readiness Analyzer", error=str(e))
        logger.error(f"❌ [{INVOCATION_ID}] TOOL run_upgrade_readiness_analyzer FAILED - {elapsed:.2f}s - {str(e)}")

        return json.dumps({
            "status": "error",
            "message": error_msg
        }, ensure_ascii=False)


# Agent will be created per-invocation to avoid concurrency issues
# (moved inside invoke() function)


@app.entrypoint
async def invoke(payload):
    """
    Orchestrator entrypoint handler with SEQUENTIAL WORKFLOW pattern

    Sequential execution flow (deterministic):
    1. Check Blue-Green deployment status
    2. Run Error Log Analyzer (waits for step 1)
    3. Run Variables Compare (waits for step 2)
    4. Run InnoDB Status Analyzer (waits for step 3)
    5. Generate summary with LLM

    Expected payload format:
    {
        "blue_host": "mysql-blue.xxx.rds.amazonaws.com",
        "green_host": "mysql-green.xxx.rds.amazonaws.com",
        "db_secret_id": "<Secrets Manager secret name or ARN>",
        "db_user": "admin",  # optional, overrides secret username
        "green_log_group": "/aws/rds/instance/mysql-green/error",
        "error_log_analyzer_arn": "arn:aws:...",
        "variables_compare_arn": "arn:aws:...",
        "upgrade_readiness_analyzer_arn": "arn:aws:...",
        "s3_bucket": "rds-mysql-upgrade-reports",  # optional
        "region": "us-east-1"  # optional
    }

    Streaming events:
    - step_start: Tool execution started
    - step_complete: Tool execution completed with result
    - summary_stream: LLM-generated summary tokens
    - workflow_complete: Final result with all data
    """
    invoke_start = time.time()
    logger.info(f"🚀 [{INVOCATION_ID}] ========== SEQUENTIAL WORKFLOW STARTED ==========")
    logger.info(f"📥 [{INVOCATION_ID}] Received payload keys: {list(payload.keys())}")

    # Extract configuration
    blue_host = payload.get("blue_host")
    green_host = payload.get("green_host")
    db_secret_id = payload.get("db_secret_id")
    db_user = payload.get("db_user")  # optional override of the secret's username
    green_log_group = payload.get("green_log_group")
    error_log_analyzer_arn = payload.get("error_log_analyzer_arn")
    variables_compare_arn = payload.get("variables_compare_arn")
    upgrade_readiness_analyzer_arn = payload.get("upgrade_readiness_analyzer_arn")
    s3_bucket = payload.get("s3_bucket", "rds-mysql-upgrade-reports")
    region = payload.get("region", "us-east-1")
    language = normalize_language(payload.get("language", "ko"))
    logger.info(f"🌐 [{INVOCATION_ID}] Report language: {language}")

    # Validate required fields
    if not all([blue_host, green_host, db_secret_id, green_log_group,
                error_log_analyzer_arn, variables_compare_arn, upgrade_readiness_analyzer_arn]):
        yield {
            "event_type": "error",
            "step": 0,
            "status": "error",
            "message": m("missing_fields", language)
        }
        return

    # Resolve the password once, in-process, only for the orchestrator's own
    # reachability check. Sub-agents receive db_secret_id (never the password).
    try:
        _orch_creds = _load_db_credentials(db_secret_id, db_user)
    except Exception as e:
        logger.error(f"❌ [{INVOCATION_ID}] Failed to load DB credentials from Secrets Manager: {type(e).__name__}")
        yield {
            "event_type": "error",
            "step": 0,
            "status": "error",
            "message": "Failed to load DB credentials from Secrets Manager. Check db_secret_id and the runtime role's secretsmanager:GetSecretValue permission.",
        }
        return
    _orch_user = _orch_creds["user"]
    _orch_password = _orch_creds["password"]

    # ============================================================
    # STEP 1: Check Blue-Green Deployment Status
    # ============================================================
    logger.info(f"🔧 [{INVOCATION_ID}] STEP 1: check_blue_green_deployment")
    yield {
        "event_type": "step_start",
        "step": 1,
        "tool": "check_blue_green_deployment",
        "message": m("s1_start", language)
    }

    step1_start = time.time()

    # Progress: Connecting to Blue
    yield {
        "event_type": "progress",
        "step": 1,
        "message": m("s1_connect", language, host=blue_host)
    }

    deployment_result_str = check_blue_green_deployment(
        blue_host=blue_host,
        green_host=green_host,
        password=_orch_password,
        username=_orch_user
    )
    deployment_result = json.loads(deployment_result_str)
    step1_elapsed = time.time() - step1_start

    yield {
        "event_type": "step_complete",
        "step": 1,
        "tool": "check_blue_green_deployment",
        "elapsed_seconds": round(step1_elapsed, 2),
        "result": deployment_result
    }

    # Check if deployment is ready
    if deployment_result.get("status") != "ready":
        yield {
            "event_type": "workflow_stopped",
            "step": 1,
            "reason": m("s1_not_ready", language),
            "deployment_status": deployment_result.get("status"),
            "message": deployment_result.get("message")
        }
        return

    # ============================================================
    # STEP 2: Run Error Log Analyzer
    # ============================================================
    logger.info(f"🔧 [{INVOCATION_ID}] STEP 2: run_error_log_analyzer")
    yield {
        "event_type": "step_start",
        "step": 2,
        "tool": "run_error_log_analyzer",
        "message": m("s2_start", language)
    }

    step2_start = time.time()

    yield {
        "event_type": "progress",
        "step": 2,
        "message": m("s2_loggroup", language, group=green_log_group)
    }

    yield {
        "event_type": "progress",
        "step": 2,
        "message": m("s2_invoke", language)
    }

    error_log_result_str = run_error_log_analyzer(
        log_group_name=green_log_group,
        agent_runtime_arn=error_log_analyzer_arn,
        s3_bucket=s3_bucket,
        region=region,
        language=language
    )
    error_log_result = json.loads(error_log_result_str)
    step2_elapsed = time.time() - step2_start

    yield {
        "event_type": "progress",
        "step": 2,
        "message": m("s2_done", language)
    }

    yield {
        "event_type": "step_complete",
        "step": 2,
        "tool": "run_error_log_analyzer",
        "elapsed_seconds": round(step2_elapsed, 2),
        "result": error_log_result
    }

    # ============================================================
    # STEP 3: Run Variables Compare
    # ============================================================
    logger.info(f"🔧 [{INVOCATION_ID}] STEP 3: run_variables_compare")
    yield {
        "event_type": "step_start",
        "step": 3,
        "tool": "run_variables_compare",
        "message": m("s3_start", language)
    }

    step3_start = time.time()

    yield {
        "event_type": "progress",
        "step": 3,
        "message": m("s3_prep", language)
    }

    yield {
        "event_type": "progress",
        "step": 3,
        "message": m("s3_invoke", language)
    }

    variables_result_str = run_variables_compare(
        blue_host=blue_host,
        green_host=green_host,
        db_secret_id=db_secret_id,
        agent_runtime_arn=variables_compare_arn,
        username=db_user,
        s3_bucket=s3_bucket,
        region=region,
        language=language
    )
    variables_result = json.loads(variables_result_str)
    step3_elapsed = time.time() - step3_start

    yield {
        "event_type": "progress",
        "step": 3,
        "message": m("s3_done", language,
                     blue=variables_result.get("blue_variable_count", 0),
                     green=variables_result.get("green_variable_count", 0))
    }

    yield {
        "event_type": "step_complete",
        "step": 3,
        "tool": "run_variables_compare",
        "elapsed_seconds": round(step3_elapsed, 2),
        "result": variables_result
    }

    # ============================================================
    # STEP 4: Run Upgrade Readiness Analyzer (InnoDB + Query Risk)
    # ============================================================
    logger.info(f"🔧 [{INVOCATION_ID}] STEP 4: run_upgrade_readiness_analyzer")
    yield {
        "event_type": "step_start",
        "step": 4,
        "tool": "run_upgrade_readiness_analyzer",
        "message": m("s4_start", language)
    }

    step4_start = time.time()

    yield {
        "event_type": "progress",
        "step": 4,
        "message": m("s4_collect", language, host=blue_host)
    }

    yield {
        "event_type": "progress",
        "step": 4,
        "message": m("s4_invoke", language)
    }

    readiness_result_str = run_upgrade_readiness_analyzer(
        blue_host=blue_host,
        db_secret_id=db_secret_id,
        agent_runtime_arn=upgrade_readiness_analyzer_arn,
        username=db_user,
        s3_bucket=s3_bucket,
        region=region,
        language=language
    )
    readiness_result = json.loads(readiness_result_str)
    step4_elapsed = time.time() - step4_start

    yield {
        "event_type": "progress",
        "step": 4,
        "message": m("s4_done", language,
                     version=readiness_result.get("mysql_version", "N/A"),
                     count=readiness_result.get("risky_queries_count", 0))
    }

    yield {
        "event_type": "step_complete",
        "step": 4,
        "tool": "run_upgrade_readiness_analyzer",
        "elapsed_seconds": round(step4_elapsed, 2),
        "result": readiness_result
    }

    # ============================================================
    # STEP 5: Generate Summary with LLM
    # ============================================================
    logger.info(f"🤖 [{INVOCATION_ID}] STEP 5: Generating summary with LLM")
    yield {
        "event_type": "step_start",
        "step": 5,
        "tool": "summary_generator",
        "message": m("s5_start", language)
    }

    yield {
        "event_type": "progress",
        "step": 5,
        "message": m("s5_init", language)
    }

    # Build report_urls for UI download buttons (names localized by language)
    report_urls = []
    if error_log_result.get("presigned_url"):
        report_urls.append({
            "name": report_name("error_log", language),
            "url": error_log_result["presigned_url"],
            "type": "error_log"
        })
    if variables_result.get("presigned_url"):
        report_urls.append({
            "name": report_name("variables", language),
            "url": variables_result["presigned_url"],
            "type": "variables"
        })
    if readiness_result.get("presigned_url"):
        report_urls.append({
            "name": report_name("upgrade_readiness", language),
            "url": readiness_result["presigned_url"],
            "type": "upgrade_readiness"
        })

    # Create summary agent (no tools, just for text generation)
    summary_system_prompt = {
        "ko": "당신은 MySQL 업그레이드 분석 결과를 요약하는 전문가입니다. 간결하고 명확하게 한글로 요약하세요.",
        "en": "You are an expert summarizing MySQL upgrade analysis results. Summarize concisely and clearly in English.",
    }[language]
    summary_agent = Agent(
        model=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        system_prompt=summary_system_prompt,
        callback_handler=None
    )

    summary_prompt = f"""다음 MySQL Blue-Green 업그레이드 분석 결과를 요약해주세요:

## 1. 배포 상태 확인 결과
- Blue 버전: {deployment_result.get('blue_version')}
- Green 버전: {deployment_result.get('green_version')}
- 상태: {deployment_result.get('status')}

## 2. 에러 로그 분석 결과
- 상태: {error_log_result.get('status')}
- 분석 기간: {error_log_result.get('hours_analyzed', 24)}시간
- 로그 이벤트: {error_log_result.get('log_events', 0)}건

## 3. 변수 비교 결과
- 상태: {variables_result.get('status')}
- Blue 변수 수: {variables_result.get('blue_variable_count', 0)}개
- Green 변수 수: {variables_result.get('green_variable_count', 0)}개

## 4. 업그레이드 준비 분석 결과 (InnoDB 상태 + 쿼리 리스크)
- 상태: {readiness_result.get('status')}
- MySQL 버전: {readiness_result.get('mysql_version', 'N/A')}
- 리스크 쿼리 수: {readiness_result.get('risky_queries_count', 0)}개
- 분석 요약: {readiness_result.get('summary', 'N/A')[:300]}

{"간단한 요약과 주요 액션 아이템 3-5개를 제시해주세요." if language == "ko" else "Provide a brief summary and 3-5 key action items. Write the entire response in English."}"""

    step5_start = time.time()
    summary_text = ""

    yield {
        "event_type": "progress",
        "step": 5,
        "message": m("s5_gen", language)
    }

    # Stream summary generation
    async for event in summary_agent.stream_async(summary_prompt):
        # Extract text from streaming event
        if hasattr(event, 'data') and isinstance(event.data, dict):
            if 'delta' in event.data and 'text' in event.data['delta']:
                text_chunk = event.data['delta']['text']
                summary_text += text_chunk
                yield {
                    "event_type": "summary_stream",
                    "step": 5,
                    "text": text_chunk
                }

    step5_elapsed = time.time() - step5_start

    yield {
        "event_type": "step_complete",
        "step": 5,
        "tool": "summary_generator",
        "elapsed_seconds": round(step5_elapsed, 2),
        "result": {"summary": summary_text}
    }

    # ============================================================
    # FINAL: Workflow Complete
    # ============================================================
    total_elapsed = time.time() - invoke_start
    logger.info(f"✅ [{INVOCATION_ID}] ========== WORKFLOW COMPLETED - {total_elapsed:.2f}s ==========")

    final_result = {
        "event_type": "workflow_complete",
        "status": "success",
        "total_elapsed_seconds": round(total_elapsed, 2),
        "deployment_check": {
            "blue_version": deployment_result.get("blue_version"),
            "green_version": deployment_result.get("green_version"),
            "status": deployment_result.get("status")
        },
        "error_log_analysis": {
            "presigned_url": error_log_result.get("presigned_url", ""),
            "status": error_log_result.get("status"),
            "log_events": error_log_result.get("log_events", 0)
        },
        "variables_comparison": {
            "presigned_url": variables_result.get("presigned_url", ""),
            "status": variables_result.get("status"),
            "blue_variable_count": variables_result.get("blue_variable_count", 0),
            "green_variable_count": variables_result.get("green_variable_count", 0)
        },
        "upgrade_readiness_analysis": {
            "presigned_url": readiness_result.get("presigned_url", ""),
            "status": readiness_result.get("status"),
            "mysql_version": readiness_result.get("mysql_version", ""),
            "risky_queries_count": readiness_result.get("risky_queries_count", 0)
        },
        "report_urls": report_urls,
        "summary": summary_text
    }

    yield final_result


if __name__ == "__main__":
    app.run()
