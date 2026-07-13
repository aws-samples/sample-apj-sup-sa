#!/usr/bin/env python3
"""
GPU Capacity Finder Agent — Short-Term GPU Reservations on AWS
===============================================================

An Amazon Bedrock Inline Agent that finds available EC2 Capacity Blocks and
SageMaker Training Plans across regions using natural language.

Uses two Bedrock Agent features:
- Skills (action groups with tool functions) for capacity search
- AMAZON.UserInput for interactive clarification when requirements are unclear

ARCHITECTURE:
    User ──▶ Bedrock InlineAgent
                 ├── AMAZON.UserInput (ask clarifying questions)
                 ├── Skill: SearchEC2CapacityBlocks
                 │       └── ec2:DescribeCapacityBlockOfferings
                 └── Skill: SearchSageMakerTrainingPlans
                         └── sagemaker:SearchTrainingPlanOfferings

REFERENCE:
    - Streamlit app: https://github.com/aws-samples/sample-capacity-finder-for-ec2-capacity-block-and-sagemaker-training-plan
    - EC2 Capacity Blocks: https://aws.amazon.com/ec2/capacityblocks/
    - SageMaker Training Plans: https://docs.aws.amazon.com/sagemaker/latest/dg/reserve-capacity-with-training-plans.html
    - AMAZON.UserInput: https://docs.aws.amazon.com/bedrock/latest/userguide/agents-enable-user-input.html
    - InvokeInlineAgent: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_InvokeInlineAgent.html

LICENSE:
    MIT-0 (MIT No Attribution) — consistent with the repository.
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"

SUPPORTED_INSTANCE_TYPES = [
    "p6-b200.48xlarge", "p6-b300.48xlarge",
    "p5.48xlarge", "p5e.48xlarge", "p5en.48xlarge",
    "p4d.24xlarge", "p4de.24xlarge",
    "trn1.32xlarge", "trn2.48xlarge", "trn2.3xlarge",
]

SEARCH_REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-north-1", "eu-west-2",
    "ap-northeast-1", "ap-northeast-2", "ap-south-1",
    "ap-southeast-2", "ap-southeast-3", "ap-southeast-4",
    "sa-east-1",
]

VALID_DURATIONS_DAYS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14] + \
                       list(range(21, 183, 7))

AGENT_INSTRUCTION = """You are a GPU capacity advisor that helps users find short-term
GPU reservations on AWS. You search both EC2 Capacity Blocks and SageMaker Training
Plans across all supported AWS regions to find the best options.

When a user asks about GPU capacity, training infrastructure, or reservations:
1. If the user hasn't specified all requirements (instance type, count, duration,
   start date), use AMAZON.UserInput to ask for the missing information.
2. Once you have the requirements, call both SearchEC2CapacityBlocks and
   SearchSageMakerTrainingPlans to find available options.
3. Present the results in a clear comparison, highlighting the cheapest option
   and earliest availability.

Supported instance types: {instance_types}
Valid durations: 1-14 days, then weekly increments up to 182 days.

Always search ALL regions to find the best availability and pricing.""".format(
    instance_types=", ".join(SUPPORTED_INSTANCE_TYPES)
)

# ---------------------------------------------------------------------------
# Skill definitions (action groups for InlineAgent)
# ---------------------------------------------------------------------------


def _build_capacity_block_skill() -> dict:
    """
    Define the EC2 Capacity Block search skill.

    This action group allows the agent to search for EC2 Capacity Block offerings
    across AWS regions.
    """
    return {
        "actionGroupName": "SearchEC2CapacityBlocks",
        "description": (
            "Search for available EC2 Capacity Block offerings (short-term GPU "
            "reservations) across AWS regions. Returns pricing, availability zones, "
            "and start/end dates."
        ),
        "actionGroupExecutor": {"customControl": "RETURN_CONTROL"},
        "functionSchema": {
            "functions": [
                {
                    "name": "search_ec2_capacity_blocks",
                    "description": (
                        "Search EC2 Capacity Block offerings across regions for a "
                        "given instance type, count, and duration."
                    ),
                    "parameters": {
                        "instance_type": {
                            "type": "string",
                            "description": f"GPU instance type. One of: {', '.join(SUPPORTED_INSTANCE_TYPES)}",
                            "required": True,
                        },
                        "instance_count": {
                            "type": "integer",
                            "description": "Number of instances to reserve (1-256)",
                            "required": True,
                        },
                        "duration_days": {
                            "type": "integer",
                            "description": "Reservation duration in days (1-182)",
                            "required": True,
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Earliest start date (YYYY-MM-DD). Defaults to today.",
                            "required": False,
                        },
                        "regions": {
                            "type": "string",
                            "description": "Comma-separated list of regions to search, or 'all' for all regions. Default: all",
                            "required": False,
                        },
                    },
                }
            ]
        },
    }


def _build_training_plan_skill() -> dict:
    """
    Define the SageMaker Training Plan search skill.

    This action group allows the agent to search for SageMaker Training Plan
    offerings across AWS regions.
    """
    return {
        "actionGroupName": "SearchSageMakerTrainingPlans",
        "description": (
            "Search for available SageMaker Training Plan offerings (short-term "
            "reserved ML training capacity) across AWS regions. Returns pricing, "
            "availability, and scheduling details."
        ),
        "actionGroupExecutor": {"customControl": "RETURN_CONTROL"},
        "functionSchema": {
            "functions": [
                {
                    "name": "search_sagemaker_training_plans",
                    "description": (
                        "Search SageMaker Training Plan offerings across regions "
                        "for a given instance type, count, and duration."
                    ),
                    "parameters": {
                        "instance_type": {
                            "type": "string",
                            "description": f"GPU instance type (without ml. prefix). One of: {', '.join(SUPPORTED_INSTANCE_TYPES)}",
                            "required": True,
                        },
                        "instance_count": {
                            "type": "integer",
                            "description": "Number of instances to reserve (1-256)",
                            "required": True,
                        },
                        "duration_days": {
                            "type": "integer",
                            "description": "Reservation duration in days (1-182)",
                            "required": True,
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Earliest start date (YYYY-MM-DD). Defaults to today.",
                            "required": False,
                        },
                        "regions": {
                            "type": "string",
                            "description": "Comma-separated list of regions to search, or 'all' for all regions. Default: all",
                            "required": False,
                        },
                    },
                }
            ]
        },
    }


def _build_user_input_skill() -> dict:
    """
    Define the AMAZON.UserInput action group.

    This built-in Bedrock capability allows the agent to ask the user clarifying
    questions when it doesn't have enough information to execute a search.
    """
    return {
        "actionGroupName": "UserInputAction",
        "parentActionGroupSignature": "AMAZON.UserInput",
        "actionGroupState": "ENABLED",
    }


# ---------------------------------------------------------------------------
# Tool execution (handles RETURN_CONTROL invocations)
# ---------------------------------------------------------------------------


def execute_ec2_capacity_search(params: dict) -> list[dict]:
    """
    Execute EC2 DescribeCapacityBlockOfferings across regions.

    Returns a list of offerings with region, AZ, pricing, and schedule.
    """
    instance_type = params["instance_type"]
    instance_count = int(params["instance_count"])
    duration_days = int(params["duration_days"])
    start_date_str = params.get("start_date")
    regions_str = params.get("regions", "all")

    start_date = (
        datetime.strptime(start_date_str, "%Y-%m-%d")
        if start_date_str
        else datetime.today()
    )

    regions = SEARCH_REGIONS if regions_str == "all" else regions_str.split(",")
    results = []

    for region in regions:
        try:
            ec2 = boto3.client("ec2", region_name=region.strip())
            api_params = {
                "InstanceType": instance_type,
                "InstanceCount": instance_count,
                "CapacityDurationHours": duration_days * 24,
                "StartDateRange": start_date,
                "MaxResults": 20,
            }
            resp = ec2.describe_capacity_block_offerings(**api_params)
            for offering in resp.get("CapacityBlockOfferings", []):
                results.append({
                    "source": "EC2 Capacity Block",
                    "region": region,
                    "availability_zone": offering.get("AvailabilityZone", "N/A"),
                    "instance_type": instance_type,
                    "instance_count": offering.get("InstanceCount", instance_count),
                    "duration_days": duration_days,
                    "start_date": str(offering.get("StartDate", "")),
                    "end_date": str(offering.get("EndDate", "")),
                    "upfront_fee": f"${offering.get('UpfrontFee', '0')}",
                })
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ("InvalidParameterValue", "Unsupported"):
                results.append({
                    "source": "EC2 Capacity Block",
                    "region": region,
                    "error": f"{code}: {e.response['Error']['Message']}",
                })
        except Exception as e:
            results.append({
                "source": "EC2 Capacity Block",
                "region": region,
                "error": str(e),
            })

    return results


def execute_sagemaker_training_plan_search(params: dict) -> list[dict]:
    """
    Execute SageMaker SearchTrainingPlanOfferings across regions.

    Returns a list of training plan offerings with region, pricing, and schedule.
    """
    instance_type = params["instance_type"]
    # SageMaker expects ml. prefix
    sm_instance_type = f"ml.{instance_type}" if not instance_type.startswith("ml.") else instance_type
    instance_count = int(params["instance_count"])
    duration_days = int(params["duration_days"])
    start_date_str = params.get("start_date")
    regions_str = params.get("regions", "all")

    start_date = (
        datetime.strptime(start_date_str, "%Y-%m-%d")
        if start_date_str
        else datetime.today()
    )

    regions = SEARCH_REGIONS if regions_str == "all" else regions_str.split(",")
    results = []

    for region in regions:
        try:
            sm = boto3.client("sagemaker", region_name=region.strip())
            api_params = {
                "TargetResources": ["training-job"],
                "InstanceType": sm_instance_type,
                "InstanceCount": instance_count,
                "StartTimeAfter": start_date,
                "DurationHours": duration_days * 24,
            }
            resp = sm.search_training_plan_offerings(**api_params)
            for offering in resp.get("TrainingPlanOfferings", []):
                reserved = offering.get("ReservedCapacityOfferings", [])
                if reserved:
                    r = reserved[0]
                    results.append({
                        "source": "SageMaker Training Plan",
                        "region": region,
                        "availability_zone": r.get("AvailabilityZone", "N/A"),
                        "instance_type": r.get("InstanceType", sm_instance_type),
                        "instance_count": r.get("InstanceCount", instance_count),
                        "duration_days": duration_days,
                        "start_date": str(r.get("StartTime", "")),
                        "end_date": str(r.get("EndTime", "")),
                        "upfront_fee": f"${offering.get('UpfrontFee', '0')}",
                    })
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ("InvalidAction", "AuthFailure", "ValidationException"):
                results.append({
                    "source": "SageMaker Training Plan",
                    "region": region,
                    "error": f"{code}: {e.response['Error']['Message']}",
                })
        except Exception as e:
            if "InvalidAction" not in str(e):
                results.append({
                    "source": "SageMaker Training Plan",
                    "region": region,
                    "error": str(e),
                })

    return results


def handle_return_control(invocation: dict) -> list[dict]:
    """
    Process a RETURN_CONTROL event from the agent.

    Dispatches to the appropriate tool function and returns results.
    """
    invocation_inputs = invocation.get("invocationInputs", [])
    results = []

    for inp in invocation_inputs:
        func_input = inp.get("functionInvocationInput", {})
        action_group = func_input.get("actionGroup", "")
        function_name = func_input.get("function", "")
        parameters = {
            p["name"]: p["value"]
            for p in func_input.get("parameters", [])
        }

        console.print(f"  [dim]Calling {action_group}.{function_name}({parameters})[/dim]")

        if function_name == "search_ec2_capacity_blocks":
            tool_results = execute_ec2_capacity_search(parameters)
        elif function_name == "search_sagemaker_training_plans":
            tool_results = execute_sagemaker_training_plan_search(parameters)
        else:
            tool_results = [{"error": f"Unknown function: {function_name}"}]

        results.append({
            "functionResult": {
                "actionGroup": action_group,
                "function": function_name,
                "responseBody": {
                    "TEXT": {"body": json.dumps(tool_results, default=str)}
                },
            }
        })

    return results


# ---------------------------------------------------------------------------
# Agent conversation loop
# ---------------------------------------------------------------------------


class GPUCapacityFinderAgent:
    """
    Conversational agent for finding short-term GPU reservations.

    Uses Bedrock InlineAgent with:
    - Two skills (EC2 Capacity Blocks + SageMaker Training Plans)
    - AMAZON.UserInput for interactive clarification
    """

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, region: str = "us-east-1"):
        self.model_id = model_id
        self.region = region
        self.client = boto3.client("bedrock-agent-runtime", region_name=region)
        self.session_id = str(uuid.uuid4())

    def _invoke(self, input_text: str, session_state: dict | None = None) -> dict:
        """Send a message to the inline agent and return the response event stream."""
        request = {
            "sessionId": self.session_id,
            "inputText": input_text,
            "foundationModel": self.model_id,
            "instruction": AGENT_INSTRUCTION,
            "agentName": "GPUCapacityFinder",
            "actionGroups": [
                _build_capacity_block_skill(),
                _build_training_plan_skill(),
                _build_user_input_skill(),
            ],
            "enableTrace": False,
        }
        if session_state:
            request["inlineSessionState"] = session_state

        response = self.client.invoke_inline_agent(**request)
        return response

    def _process_stream(self, response: dict) -> tuple[str, dict | None]:
        """
        Process the response event stream.

        Returns (agent_text, return_control_payload) where return_control_payload
        is None if the agent finished, or contains the invocation details if the
        agent needs tool results or user input.
        """
        output_text = ""
        return_control = None

        for event in response.get("completion", []):
            if "chunk" in event:
                chunk_bytes = event["chunk"].get("bytes", b"")
                output_text += chunk_bytes.decode("utf-8")
            elif "returnControl" in event:
                return_control = event["returnControl"]

        return output_text, return_control

    def chat(self, user_message: str) -> str:
        """
        Send a message and handle the full conversation loop.

        Automatically handles:
        - RETURN_CONTROL (tool invocations) → execute tool → return results
        - AMAZON.UserInput → prompt user → send answer back
        - Final response → return agent text
        """
        current_input = user_message
        session_state = None

        while True:
            response = self._invoke(current_input, session_state)
            output_text, return_control = self._process_stream(response)

            if return_control is None:
                # Agent is done — return the final text
                return output_text

            invocation_id = return_control.get("invocationId", "")

            # Check if it's a user input request
            if "invocationInputs" in return_control:
                first_input = return_control["invocationInputs"][0]
                if "functionInvocationInput" in first_input:
                    # Tool invocation — execute and return results
                    console.print("\n[bold cyan]⚙️  Executing search...[/bold cyan]")
                    tool_results = handle_return_control(return_control)

                    session_state = {
                        "invocationId": invocation_id,
                        "returnControlInvocationResults": tool_results,
                    }
                    current_input = ""  # empty input with results
                    continue

            # AMAZON.UserInput — the agent wants to ask the user something
            if output_text:
                console.print(f"\n[bold green]Agent:[/bold green] {output_text}")

            # Get user input
            try:
                user_answer = input("\nYou: ").strip()
            except (KeyboardInterrupt, EOFError):
                return "Conversation ended by user."

            if not user_answer:
                return "No input provided."

            # Send the user's answer back
            current_input = user_answer
            session_state = None

    def run_interactive(self, initial_query: str | None = None):
        """Run an interactive conversation loop."""
        console.print(Panel(
            "[bold]GPU Capacity Finder Agent[/bold]\n\n"
            "Find short-term GPU reservations on AWS (EC2 Capacity Blocks\n"
            "and SageMaker Training Plans) using natural language.\n\n"
            "Type 'quit' or 'exit' to end the conversation.",
            title="🔎 GPU Capacity Finder",
            border_style="cyan",
        ))

        if initial_query:
            console.print(f"\n[bold blue]You:[/bold blue] {initial_query}")
            response = self.chat(initial_query)
            console.print(f"\n[bold green]Agent:[/bold green] {response}")

        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if user_input.lower() in ("quit", "exit", "q"):
                break

            if not user_input:
                continue

            response = self.chat(user_input)
            console.print(f"\n[bold green]Agent:[/bold green] {response}")

        console.print("\n[dim]Goodbye![/dim]")


# ---------------------------------------------------------------------------
# Non-interactive mode (direct search, no AMAZON.UserInput)
# ---------------------------------------------------------------------------


def run_direct_search(
    instance_type: str, instance_count: int, duration_days: int, region: str | None = None
):
    """Run a direct search without the agent (useful for scripting/CI)."""
    console.print(Panel(
        f"[bold]Direct GPU Capacity Search[/bold]\n\n"
        f"Instance type:  {instance_type}\n"
        f"Count:          {instance_count}\n"
        f"Duration:       {duration_days} days\n"
        f"Start:          {datetime.today().strftime('%Y-%m-%d')}\n"
        f"Regions:        {'all' if not region else region}",
        title="🔎 Search Parameters",
    ))

    params = {
        "instance_type": instance_type,
        "instance_count": str(instance_count),
        "duration_days": str(duration_days),
        "regions": region or "all",
    }

    console.print("\n[cyan]Searching EC2 Capacity Blocks...[/cyan]")
    ec2_results = execute_ec2_capacity_search(params)
    ec2_offerings = [r for r in ec2_results if "error" not in r]

    console.print("[cyan]Searching SageMaker Training Plans...[/cyan]")
    sm_results = execute_sagemaker_training_plan_search(params)
    sm_offerings = [r for r in sm_results if "error" not in r]

    # Display results
    all_offerings = ec2_offerings + sm_offerings
    if not all_offerings:
        console.print("\n[yellow]No offerings found. Try different parameters or check more regions.[/yellow]")
        return

    table = Table(title=f"Available GPU Reservations ({len(all_offerings)} found)")
    table.add_column("Source", style="cyan")
    table.add_column("Region")
    table.add_column("AZ")
    table.add_column("Instances", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Start Date")
    table.add_column("Fee", justify="right", style="green")

    for o in sorted(all_offerings, key=lambda x: x.get("upfront_fee", "$0")):
        table.add_row(
            o["source"],
            o["region"],
            o.get("availability_zone", "N/A"),
            str(o.get("instance_count", "")),
            f"{o.get('duration_days', '')} days",
            o.get("start_date", "N/A")[:10],
            o.get("upfront_fee", "N/A"),
        )

    console.print(table)

    # Show errors if any
    errors = [r for r in ec2_results + sm_results if "error" in r]
    if errors:
        console.print(f"\n[dim]{len(errors)} region(s) returned errors (insufficient permissions or unsupported).[/dim]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description=(
            "GPU Capacity Finder Agent — Find short-term GPU reservations on AWS.\n\n"
            "Uses Amazon Bedrock InlineAgent with Skills (action groups) and\n"
            "AMAZON.UserInput for interactive conversational search."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID,
                        help=f"Foundation model (default: {DEFAULT_MODEL_ID})")
    parser.add_argument("--region", default="us-east-1",
                        help="AWS region for the agent (default: us-east-1)")
    parser.add_argument("--query", default=None,
                        help="Initial query to the agent")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Run direct search without agent (no AMAZON.UserInput)")
    parser.add_argument("--instance-type", default="p5.48xlarge",
                        help="Instance type for direct search (default: p5.48xlarge)")
    parser.add_argument("--instance-count", type=int, default=1,
                        help="Instance count for direct search (default: 1)")
    parser.add_argument("--days", type=int, default=7,
                        help="Duration in days for direct search (default: 7)")

    args = parser.parse_args()

    if args.non_interactive:
        run_direct_search(
            instance_type=args.instance_type,
            instance_count=args.instance_count,
            duration_days=args.days,
            region=None,  # search all
        )
    else:
        agent = GPUCapacityFinderAgent(model_id=args.model_id, region=args.region)
        agent.run_interactive(initial_query=args.query)


if __name__ == "__main__":
    main()
