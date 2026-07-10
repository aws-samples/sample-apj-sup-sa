import time

from voice.config import get_boto3_session


async def query_cloudwatch_logs(params, log_group_name: str, filter_pattern: str = "", minutes_back: int = 30):
    """Query recent CloudWatch logs for a log group.

    Args:
        log_group_name: The CloudWatch log group to search (e.g. /aws/lambda/voice-demo-get-users).
        filter_pattern: CloudWatch filter pattern (e.g. "ERROR" or "{ $.level = \"ERROR\" }").
        minutes_back: How many minutes of history to search. Default 30.
    """
    try:
        session = get_boto3_session()
        logs_client = session.client("logs")

        start_time = int((time.time() - minutes_back * 60) * 1000)
        end_time = int(time.time() * 1000)

        response = logs_client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time,
            endTime=end_time,
            filterPattern=filter_pattern,
            limit=50,
        )

        events = response.get("events", [])
        if not events:
            result = f"No log events matching '{filter_pattern}' in {log_group_name} (last {minutes_back}min)."
        else:
            lines = [e["message"].strip() for e in events[-20:]]
            result = "\n".join(lines)

        await params.result_callback({"logs": result})
    except Exception as e:
        await params.result_callback({"error": str(e)})
