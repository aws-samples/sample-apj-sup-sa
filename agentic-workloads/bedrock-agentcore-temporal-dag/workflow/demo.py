"""DAF demo CLI — starts a Temporal Workflow and displays live progress."""

import asyncio
import os
import sys
import time
from uuid import uuid4

from temporalio.client import Client


TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "")
TEMPORAL_API_KEY = os.environ.get("TEMPORAL_API_KEY", "")

STEPS = ["gather", "analyze", "evaluate", "re_analyze", "synthesize"]

STATUS_ICONS = {
    "running": "⏳",
    "completed": "✅",
    "skipped": "⏭️",
}


def _render_status(status: dict) -> str:
    lines = []
    for step in STEPS:
        state = status.get(step, "pending")
        icon = STATUS_ICONS.get(state, "⬜")
        lines.append(f"  {icon} {step}: {state}")
    return "\n".join(lines)


async def run(query: str):
    if not TEMPORAL_ADDRESS or not TEMPORAL_NAMESPACE:
        print("ERROR: set TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE environment variables")
        sys.exit(1)
    if not TEMPORAL_API_KEY:
        print("ERROR: set TEMPORAL_API_KEY environment variable")
        sys.exit(1)

    print(f"Connecting to Temporal Cloud ({TEMPORAL_NAMESPACE})...")
    client = await Client.connect(
        TEMPORAL_ADDRESS,
        namespace=TEMPORAL_NAMESPACE,
        api_key=TEMPORAL_API_KEY,
        tls=True,
    )

    workflow_id = f"demo-{uuid4().hex[:8]}"
    print(f"\n{'='*60}")
    print(f"  Research Pipeline")
    print(f"  Query: {query}")
    print(f"  Workflow ID: {workflow_id}")
    print(f"{'='*60}\n")

    handle = await client.start_workflow(
        "ResearchPipelineWorkflow",
        args=[{"query": query}],
        id=workflow_id,
        task_queue="daf-orchestrator",
    )

    print("DAG: gather → [analyze | evaluate] → (re_analyze?) → synthesize\n")
    print("Progress:")

    prev_status = {}
    while True:
        try:
            status = await handle.query("get_status")
        except Exception:
            status = prev_status

        if status != prev_status:
            sys.stdout.write(f"\033[{len(STEPS)+1}A\033[J" if prev_status else "")
            print(_render_status(status))
            prev_status = status

        desc = await handle.describe()
        if desc.status != 1:  # not RUNNING
            break

        await asyncio.sleep(2)

    print(f"\n{'='*60}")

    if desc.status == 2:  # COMPLETED
        result = await handle.result()
        print("  Result:")
        print(f"{'='*60}\n")

        if isinstance(result, dict) and "result" in result:
            print(result["result"])
        else:
            import json
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif desc.status == 3:  # FAILED
        print("  FAILED")
        print(f"{'='*60}")
    else:
        print(f"  Status: {desc.status}")
        print(f"{'='*60}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python demo.py <query>")
        print("Example: python demo.py 'Investigate multi-agent design patterns for generative AI'")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    asyncio.run(run(query))


if __name__ == "__main__":
    main()
