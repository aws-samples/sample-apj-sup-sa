import json
import time
from datetime import datetime, timezone

from voice.config import get_boto3_session


async def get_xray_trace_summaries(
    params,
    filter_expression: str = "responsetime > 1 OR error = true OR fault = true",
    minutes_back: int = 30,
):
    """Get recent X-Ray trace summaries matching a filter.

    Args:
        filter_expression: X-Ray filter expression. Default finds errors, faults, or slow traces.
        minutes_back: How many minutes of history to search. Default 30.
    """
    try:
        session = get_boto3_session()
        xray_client = session.client("xray")

        now = datetime.now(timezone.utc)
        start = datetime.fromtimestamp(time.time() - minutes_back * 60, tz=timezone.utc)

        response = xray_client.get_trace_summaries(
            StartTime=start,
            EndTime=now,
            FilterExpression=filter_expression,
            Sampling=False,
        )

        summaries = response.get("TraceSummaries", [])
        if not summaries:
            await params.result_callback({"traces": f"No traces matching '{filter_expression}' in the last {minutes_back} minutes."})
            return

        results = []
        for s in summaries[:10]:
            results.append({
                "trace_id": s["Id"],
                "duration_s": s.get("Duration"),
                "has_error": s.get("HasError"),
                "has_fault": s.get("HasFault"),
                "http_status": s.get("Http", {}).get("HttpStatus"),
                "url": s.get("Http", {}).get("HttpURL"),
            })

        await params.result_callback({"traces": json.dumps(results, indent=2)})
    except Exception as e:
        await params.result_callback({"error": str(e)})
