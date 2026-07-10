"""
Aurora MySQL Error Log Analyzer Agent
Reads error log events from the Green cluster's CloudWatch Logs group
(/aws/rds/cluster/<green-cluster-id>/error).
Analyzes logs and returns upgrade considerations for Aurora MySQL 3.04 → 3.10
(minor upgrade, MySQL 8.0-compatible on both sides).
Returns text response, no report generation
"""

import os
import re
import logging
import time
import uuid
from datetime import datetime, timedelta
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import ClientError

# Agent name for metrics tracking
AGENT_NAME = "aurora_error_log_analyzer"


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

# Global storage for log data and report URLs
_error_logs = None
_report_urls = {"s3_url": None, "presigned_url": None}
# Report language, set by invoke() so save_report can localize the disclaimer.
_report_language: str = "ko"


@tool
def fetch_cloudwatch_logs(log_group_name: str, log_stream_name: str = None,
                          hours_ago: int = 24, max_events: int = 1000,
                          region: str = "us-east-1") -> str:
    """
    Fetch Aurora MySQL error logs from CloudWatch Logs

    Args:
        log_group_name: CloudWatch Logs group name. For Aurora MySQL this is per-cluster:
            /aws/rds/cluster/<green-cluster-id>/error (requires "Error log" enabled in
            the cluster's Log exports — log group does not exist otherwise).
        log_stream_name: Log stream name (default: None, auto-detect latest stream)
        hours_ago: Number of hours of logs to retrieve (default: 24)
        max_events: Maximum number of log events to retrieve (default: 1000)
        region: AWS region (default: us-east-1)

    Returns:
        Success message with log count
    """
    global _error_logs

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL fetch_cloudwatch_logs STARTED - log_group={log_group_name}, stream={log_stream_name}")
    start_time = time.time()

    try:
        logs_client = boto3.client('logs', region_name=region)

        # If log_stream_name is not provided, find the latest stream
        if not log_stream_name:
            logger.info(f"📋 No log stream specified, finding latest stream...")
            streams_response = logs_client.describe_log_streams(
                logGroupName=log_group_name,
                orderBy='LastEventTime',
                descending=True,
                limit=1
            )
            streams = streams_response.get('logStreams', [])
            if not streams:
                return f"❌ No log streams found in log group: {log_group_name}"

            log_stream_name = streams[0]['logStreamName']
            logger.info(f"📋 Using latest stream: {log_stream_name}")

        # Calculate time range
        end_time = datetime.now()
        start_time_dt = end_time - timedelta(hours=hours_ago)
        start_timestamp = int(start_time_dt.timestamp() * 1000)
        end_timestamp = int(end_time.timestamp() * 1000)

        logger.info(f"📅 Fetching logs from {start_time_dt.isoformat()} to {end_time.isoformat()}")

        # Fetch log events
        all_events = []
        next_token = None

        while len(all_events) < max_events:
            params = {
                'logGroupName': log_group_name,
                'logStreamName': log_stream_name,
                'startTime': start_timestamp,
                'endTime': end_timestamp,
                'limit': min(max_events - len(all_events), 10000)  # CloudWatch limit is 10000 per request
            }

            if next_token:
                params['nextToken'] = next_token

            try:
                response = logs_client.get_log_events(**params)
                events = response.get('events', [])

                if not events:
                    break

                all_events.extend(events)

                # Check if there are more logs
                next_forward_token = response.get('nextForwardToken')
                if next_token == next_forward_token:
                    # No more logs
                    break
                next_token = next_forward_token

            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == 'ResourceNotFoundException':
                    logger.warning(f"⚠️  Log stream not found: {log_stream_name}")
                    return f"❌ Log stream not found: {log_stream_name} in log group {log_group_name}"
                else:
                    raise

        # Convert events to log text
        log_lines = []
        for event in all_events:
            timestamp = datetime.fromtimestamp(event['timestamp'] / 1000).isoformat()
            message = event['message']
            log_lines.append(f"[{timestamp}] {message}")

        _error_logs = "\n".join(log_lines)

        elapsed = time.time() - start_time
        result_msg = f"✅ Fetched {len(all_events)} log events from CloudWatch Logs ({len(_error_logs)} characters)"
        logger.info(f"✅ [{INVOCATION_ID}] TOOL fetch_cloudwatch_logs COMPLETED - {elapsed:.2f}s - {len(all_events)} events")
        return result_msg

    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = f"❌ Failed to fetch CloudWatch logs: {str(e)}"
        logger.error(f"❌ [{INVOCATION_ID}] TOOL fetch_cloudwatch_logs FAILED - {elapsed:.2f}s - {str(e)}")
        return error_msg


@tool
def get_error_logs() -> str:
    """
    Get the fetched error logs for analysis

    Returns:
        The error log content as text
    """
    global _error_logs

    logger.info(f"🔧 [{INVOCATION_ID}] TOOL get_error_logs STARTED")
    start_time = time.time()

    if not _error_logs:
        logger.warning(f"⚠️  [{INVOCATION_ID}] TOOL get_error_logs FAILED - No logs fetched")
        return "❌ No error logs have been fetched. Please call fetch_cloudwatch_logs first."

    elapsed = time.time() - start_time
    logger.info(f"✅ [{INVOCATION_ID}] TOOL get_error_logs COMPLETED - {elapsed:.2f}s - {len(_error_logs)} chars")

    return _error_logs


# Timestamp labels the LLM tends to emit (KO + EN), used to strip any
# generation date/time from report bodies. The S3 object name already carries
# a real timestamp, so an LLM-invented date in the body is pure noise.
# NOTE: "분석 기간" (log time range) is intentionally NOT here — it is real data.
_TIMESTAMP_LABELS = (
    "생성 일시", "수집 일시", "분석 일시", "작성 일시",
    "Report generated", "Generated at", "Generated on",
    "Collected at", "Analyzed at", "Date generated",
)


def _strip_report_time(markdown: str) -> str:
    """Remove any generation date line the LLM added to the report.

    The prompt tells the model not to print a date, but LLMs add one anyway (and
    hallucinate the year). We delete it deterministically:
      - a standalone bold line like `**Report generated:** ...`  → drop the line
      - an inline segment like `... | Generated at: X | ...`      → drop segment
    Only timestamp-labeled text is touched, so the log time range ("분석 기간")
    and data values are never altered.
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
def save_report(markdown_content: str, s3_bucket: str = "aurora-mysql-upgrade-reports",
                s3_key_prefix: str = "error-log-analysis") -> str:
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
    import json

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
        s3_key = f"{s3_key_prefix}/error_log_analysis_{timestamp}.md"

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


@tool
def list_log_streams(log_group_name: str, region: str = "us-east-1", max_streams: int = 20) -> str:
    """
    List available log streams in a CloudWatch Logs group

    Args:
        log_group_name: CloudWatch Logs group name
        region: AWS region (default: us-east-1)
        max_streams: Maximum number of streams to list (default: 20)

    Returns:
        List of log stream names
    """
    logger.info(f"🔧 [{INVOCATION_ID}] TOOL list_log_streams STARTED - log_group={log_group_name}")
    start_time = time.time()

    try:
        logs_client = boto3.client('logs', region_name=region)

        response = logs_client.describe_log_streams(
            logGroupName=log_group_name,
            orderBy='LastEventTime',
            descending=True,
            limit=max_streams
        )

        streams = response.get('logStreams', [])

        if not streams:
            return f"❌ No log streams found in log group: {log_group_name}"

        stream_list = []
        for stream in streams:
            stream_name = stream['logStreamName']
            last_event_time = stream.get('lastEventTimestamp')
            if last_event_time:
                last_event = datetime.fromtimestamp(last_event_time / 1000).isoformat()
                stream_list.append(f"  - {stream_name} (last event: {last_event})")
            else:
                stream_list.append(f"  - {stream_name}")

        result = f"✅ Found {len(streams)} log streams in {log_group_name}:\n" + "\n".join(stream_list)

        elapsed = time.time() - start_time
        logger.info(f"✅ [{INVOCATION_ID}] TOOL list_log_streams COMPLETED - {elapsed:.2f}s - {len(streams)} streams")
        return result

    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = f"❌ Failed to list log streams: {str(e)}"
        logger.error(f"❌ [{INVOCATION_ID}] TOOL list_log_streams FAILED - {elapsed:.2f}s - {str(e)}")
        return error_msg


# Agent will be created per-invocation to avoid concurrency issues
# (moved inside invoke() function)


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
            "- Keep MySQL/Aurora identifiers, log messages, SQL, and metric "
            "names unchanged.\n"
        )
    return (
        "**출력 언어: 한국어**\n"
        "- 리포트 전체(제목, 모든 섹션 헤더, 표 헤더, 설명, 권고)를 한국어로 작성하세요.\n"
    )


@app.entrypoint
async def invoke(payload):
    """
    AgentCore entrypoint handler

    Expected payload format:
    {
        "log_group_name": "/aws/rds/cluster/<green-cluster-id>/error",
        "log_stream_name": None,  # optional, auto-detect latest stream if not provided
        "hours_ago": 24,  # optional, default: 24
        "max_events": 1000,  # optional, default: 1000
        "region": "us-east-1",  # optional, default: us-east-1
        "s3_bucket": "aurora-mysql-upgrade-reports"  # optional, for report storage
    }
    """
    invoke_start = time.time()
    logger.info(f"🚀 [{INVOCATION_ID}] ========== INVOKE FUNCTION STARTED ==========")
    logger.info(f"📥 [{INVOCATION_ID}] Received payload keys: {list(payload.keys())}")

    # Extract configuration from payload
    log_group_name = payload.get("log_group_name")
    log_stream_name = payload.get("log_stream_name")  # None = auto-detect
    hours_ago = payload.get("hours_ago", 24)
    max_events = payload.get("max_events", 1000)
    region = payload.get("region", "us-east-1")
    s3_bucket = payload.get("s3_bucket", "aurora-mysql-upgrade-reports")
    language = payload.get("language", "ko")  # "ko" or "en"
    global _report_language
    _report_language = language
    language_directive = build_language_directive(language)

    if not log_group_name:
        return {
            "error": "Missing required configuration",
            "message": "log_group_name is required (e.g., /aws/rds/cluster/<green-cluster-id>/error)"
        }

    # Build agent message
    if log_stream_name:
        stream_info = log_stream_name
    else:
        stream_info = ("자동 감지 (최신 스트림)" if language == "ko"
                       else "auto-detected (latest stream)")
    agent_message = f"""
{language_directive}
Aurora MySQL Error Log 분석 및 마이너 업그레이드 (3.04 → 3.10) 주의사항 추출 후 S3에 마크다운 보고서 저장.

**컨텍스트:** Aurora MySQL 3.04 → 3.10 마이너 업그레이드 (양쪽 모두 MySQL 8.0 호환).
메이저 업그레이드처럼 deprecated/removed 기능이 한꺼번에 쏟아지지는 않으며,
이슈는 보통 마이너 패치에 묶인 동작 변경, Aurora 옵션 default 변경, 재시작/페일오버
영향, 누적 deprecation 경고에서 발생합니다.

**작업:**
1. fetch_cloudwatch_logs로 CloudWatch Logs에서 Aurora MySQL error log 수집
   - log_group_name: {log_group_name}
   - log_stream_name: {stream_info}
   - hours_ago: {hours_ago}
   - max_events: {max_events}
   - region: {region}
2. get_error_logs로 로그 데이터 조회
3. 로그를 분석하여 Aurora MySQL 3.04 → 3.10 마이너 업그레이드 시 주의사항 추출 (마크다운 형식)
4. save_report로 S3에 저장 (s3_bucket="{s3_bucket}")
   - save_report는 JSON 형태로 s3_url과 presigned_url을 반환함
   - 최종 응답에 presigned_url을 포함해서 사용자에게 전달

**분석 규칙:**
- 출력 언어는 위의 "출력 언어" 지시를 따를 것
- 로그에서 발견된 에러, 경고, deprecated 메시지 분석
- Aurora MySQL 3.10 (또는 사이의 마이너 패치)에서 변경된 사항과 관련된 내용 강조
- 업그레이드 시 발생할 수 있는 잠재적 문제 식별
- 구체적인 액션 아이템 제시

**마크다운 리포트 작성 규칙:**
- 출력 언어는 위의 "출력 언어" 지시를 따를 것
- **리포트 생성 일시·날짜를 넣지 말 것 (예: "생성 일시", "Report generated" 등 금지). 단, "분석 기간"(로그 조회 기간)은 실제 로그 데이터 기준으로 유지할 것.**
- 로그가 없거나 특별한 문제가 없으면 그 사실을 명확히 알려줄 것
- 발견된 문제가 없어도 일반적인 Aurora MySQL 마이너 업그레이드 점검 항목
  (RDS 이벤트 알림, 마이너 패치 노트, 재시작/페일오버 영향, 누적 deprecation 경고 등)
  은 제공할 것
- Aurora 클러스터의 로그 그룹은 모든 인스턴스 로그가 stream 으로 분리되어
  있으며, 본 분석은 자동 선택된 단일 stream(보통 writer 또는 최근 활동 stream)
  만을 대상으로 한다는 점을 리포트 1번 섹션에 한 줄 명시할 것.

**리포트 구조:**
```markdown
# Aurora MySQL Error Log 분석 결과

## 1. 로그 요약
- 분석 기간: [시작 시간] ~ [종료 시간]
- 총 로그 이벤트: N개
- 에러 수: X개
- 경고 수: Y개

## 2. 주요 발견 사항

### 2.1 에러 메시지
[발견된 에러 메시지와 설명]

### 2.2 경고 메시지
[발견된 경고 메시지와 설명]

### 2.3 Deprecated 기능
[deprecated된 기능 및 제거 예정 기능]

## 3. Aurora MySQL 3.10 마이너 업그레이드 주의사항

### 3.1 호환성 문제
[발견된 호환성 문제와 해결 방안]

### 3.2 성능 관련
[성능에 영향을 줄 수 있는 변경사항]

### 3.3 보안 관련
[보안 관련 주의사항]

## 4. 액션 아이템
1. [우선순위 높음] 구체적인 조치 사항
2. [우선순위 중간] 구체적인 조치 사항
3. [우선순위 낮음] 구체적인 조치 사항

## 5. 추가 리소스
- 관련 Aurora MySQL release notes / RDS 마이너 업그레이드 문서 링크
- 추가 확인이 필요한 항목
```

**중요: 반드시 save_report 도구를 호출하여 위 마크다운 리포트를 S3에 저장하고, presigned_url을 최종 응답에 포함할 것**
"""

    # Create agent with tools (per-invocation to avoid concurrency issues).
    # Bedrock streaming responses can be long; bump read_timeout above the
    # boto3 default of 60s.
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
        tools=[fetch_cloudwatch_logs, get_error_logs, save_report, list_log_streams],
        model=bedrock_model,
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
        "log_group_name": log_group_name,
        "log_stream_name": log_stream_name,
        "hours_analyzed": hours_ago
    }

    # Always include presigned URL from global storage
    if presigned_url:
        response["presigned_url"] = presigned_url
        response["s3_url"] = s3_url
        logger.info(f"✅ [{INVOCATION_ID}] Presigned URL included in response")

    # Include log stats if available
    global _error_logs
    if _error_logs:
        response["log_size_chars"] = len(_error_logs)
        response["log_lines"] = _error_logs.count("\n") + 1

    invoke_elapsed = time.time() - invoke_start
    logger.info(f"🏁 [{INVOCATION_ID}] ========== INVOKE FUNCTION COMPLETED - {invoke_elapsed:.2f}s ==========")
    logger.info(f"📤 [{INVOCATION_ID}] Response keys: {list(response.keys())}")

    return response


if __name__ == "__main__":
    app.run()
