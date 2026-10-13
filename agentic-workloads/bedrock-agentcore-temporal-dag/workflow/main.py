import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from flows.research_pipeline import ResearchPipelineWorkflow
from activities import invoke_agent


async def main():
    print("Connecting to Temporal Cloud...", flush=True)
    client = await Client.connect(
        os.environ["TEMPORAL_ADDRESS"],
        namespace=os.environ["TEMPORAL_NAMESPACE"],
        api_key=os.environ.get("TEMPORAL_API_KEY"),
        tls=True,
    )
    print(f"Connected. Namespace: {os.environ['TEMPORAL_NAMESPACE']}", flush=True)

    worker = Worker(
        client,
        task_queue="daf-orchestrator",
        workflows=[ResearchPipelineWorkflow],
        activities=[invoke_agent],
    )

    print("Worker started. Listening on task queue: daf-orchestrator", flush=True)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
