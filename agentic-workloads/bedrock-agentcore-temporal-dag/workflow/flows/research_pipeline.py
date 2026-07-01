from datetime import timedelta
import asyncio

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class ResearchPipelineWorkflow:
    def __init__(self):
        self._status: dict[str, str] = {}

    @workflow.run
    async def run(self, input_data: dict) -> dict:
        # Step 1: gather
        self._status["gather"] = "running"
        gathered = await workflow.execute_activity(
            "invoke_agent",
            args=["gather", input_data],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        self._status["gather"] = "completed"

        # Step 2: fan-out (analyze + evaluate 並列)
        self._status["analyze"] = "running"
        self._status["evaluate"] = "running"

        analyzed, evaluated = await asyncio.gather(
            workflow.execute_activity(
                "invoke_agent",
                args=["analyze", gathered],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    backoff_coefficient=2.0,
                    initial_interval=timedelta(seconds=2),
                ),
            ),
            workflow.execute_activity(
                "invoke_agent",
                args=["evaluate", gathered],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=2),
            ),
        )
        self._status["analyze"] = "completed"
        self._status["evaluate"] = "completed"

        # Step 3: 条件分岐 — スコアが低い場合のみ再分析
        score = evaluated.get("score", 1.0)
        if score < 0.7:
            self._status["re_analyze"] = "running"
            analyzed = await workflow.execute_activity(
                "invoke_agent",
                args=["analyze", {
                    "original": analyzed,
                    "feedback": evaluated.get("feedback", ""),
                }],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            self._status["re_analyze"] = "completed"
        else:
            self._status["re_analyze"] = "skipped"

        # Step 4: fan-in (synthesize)
        self._status["synthesize"] = "running"
        result = await workflow.execute_activity(
            "invoke_agent",
            args=["synthesize", {
                "analysis": analyzed,
                "evaluation": evaluated,
            }],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        self._status["synthesize"] = "completed"

        return result

    @workflow.query
    def get_status(self) -> dict:
        return self._status
