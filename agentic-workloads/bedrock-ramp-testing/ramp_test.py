#!/usr/bin/env python3
"""
Amazon Bedrock TPM/RPM Ramp Testing Tool
=========================================

Tests and validates your Amazon Bedrock throughput limits using the recommended
ramp-up procedure from the AWS documentation:
https://docs.aws.amazon.com/bedrock/latest/userguide/scaling-throughput-best-practices.html

CONTEXT:
    Amazon Bedrock provides generous default TPM (Tokens Per Minute) and RPM
    (Requests Per Minute) quotas for on-demand models — these high limits reflect
    the platform's ability to scale to meet demand. However, like any distributed
    inference system, the underlying compute fleet benefits from gradual warm-up to
    deliver peak throughput consistently. This tool automates the recommended ramp-up
    procedure so you can confidently reach your target volume.

RECOMMENDED RAMP-UP PROCEDURE (from AWS docs):
    1. Start at your target request volume, e.g. 500 RPM.
    2. If you receive 503 responses, reduce your rate by 50%.
    3. Continue reducing by that factor until you reach a steady state where
       requests succeed consistently.
    4. Hold at that steady state for a short duration (e.g. 15 minutes).
    5. Increase throughput by 50% and hold for another 15 minutes.
    6. Repeat until you reach your target volume.

    Example: If your target is 2,000 RPM but you receive 503 errors, reduce to
    1,000 RPM. If errors persist, reduce to 500 RPM. Once requests succeed
    consistently at 500 RPM, hold for 15 minutes, then scale to 750, then 1,125,
    and so on until you reach 2,000 RPM.

ENDPOINTS:
    This tool supports two Amazon Bedrock endpoints:

    1. bedrock-runtime (default) — Uses the Converse API via boto3.
       Best for: native AWS SDK integration, IAM auth, VPC endpoints.

    2. bedrock-mantle — Uses the OpenAI-compatible Responses/Chat Completions API.
       Best for: migrating from OpenAI, using OpenAI SDKs, testing the Mantle
       distributed inference engine. Requires a Bedrock API key.
       Endpoint: https://bedrock-mantle.<region>.api.aws/v1
       Docs: https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-mantle.html

    Note: bedrock-mantle has SEPARATE quotas from bedrock-runtime. Ramp testing
    each endpoint independently is recommended.

USAGE:
    # Using bedrock-runtime (Converse API, default)
    python ramp_test.py --region us-east-1 --target-rpm 2000 --model-id us.moonshotai.kimi-k2.5

    # Using bedrock-mantle (OpenAI-compatible API)
    python ramp_test.py --region us-east-1 --target-rpm 2000 --model-id us.moonshotai.kimi-k2.5 \
        --endpoint mantle --api-key <your-bedrock-api-key>

LICENSE:
    MIT-0 (MIT No Attribution) — consistent with the repository.
"""

import argparse
import json
import math
import os
import sys
import time
import threading
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default model: Moonshot AI Kimi K2.5 — a capable open-source model available
# on Amazon Bedrock. You can substitute any Bedrock-supported model ID.
DEFAULT_MODEL_ID = "us.moonshotai.kimi-k2.5"

# Test prompt: short enough to keep token usage predictable, long enough to
# exercise the model meaningfully.
TEST_PROMPT = (
    "Explain in one sentence why distributed systems benefit from gradual "
    "traffic ramp-up when scaling to high throughput."
)

console = Console()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PhaseResult:
    """Result of a single ramp phase (hold period at a given RPM)."""
    target_rpm: float
    actual_rpm: float
    duration_seconds: float
    total_requests: int
    successful_requests: int
    throttled_requests: int  # 503 / ThrottlingException
    other_errors: int
    error_rate: float
    latency_p50_ms: float = 0.0
    latency_p90_ms: float = 0.0
    latency_p99_ms: float = 0.0
    steady_state: bool = False


@dataclass
class RampTestResult:
    """Overall ramp test result."""
    model_id: str
    region: str
    target_rpm: float
    achieved_rpm: float = 0.0
    phases: list = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    success: bool = False


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

class BedrockRampTester:
    """
    Implements the recommended ramp-up procedure for Amazon Bedrock throughput testing.

    Supports two endpoints:
    - bedrock-runtime: Native AWS Converse API (boto3, IAM auth)
    - bedrock-mantle: OpenAI-compatible API (OpenAI SDK, API key auth)

    The procedure automatically finds your effective steady-state throughput and
    gradually scales up to your target RPM, holding at each level to confirm
    stability before proceeding.
    """

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        region: str = "us-east-1",
        target_rpm: float = 500,
        hold_minutes: float = 15,
        reduction_factor: float = 0.5,
        increase_factor: float = 1.5,
        error_threshold: float = 0.05,
        max_tokens: int = 100,
        dry_run: bool = False,
        endpoint: str = "runtime",  # "runtime" or "mantle"
        api_key: str | None = None,
        max_requests: int | None = None,
    ):
        self.model_id = model_id
        self.region = region
        self.target_rpm = target_rpm
        self.hold_minutes = hold_minutes
        self.reduction_factor = reduction_factor
        self.increase_factor = increase_factor
        self.error_threshold = error_threshold
        self.max_tokens = max_tokens
        self.dry_run = dry_run
        self.endpoint = endpoint
        self.api_key = api_key
        self.max_requests = max_requests
        self._total_requests_all_phases = 0  # running total for budget cap

        if not dry_run:
            if endpoint == "mantle":
                if not HAS_OPENAI:
                    console.print(
                        "[red]Error: openai package required for bedrock-mantle endpoint.[/red]\n"
                        "Install with: pip install openai"
                    )
                    sys.exit(1)
                key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("BEDROCK_API_KEY")
                if not key:
                    console.print(
                        "[red]Error: API key required for bedrock-mantle endpoint.[/red]\n"
                        "Pass --api-key or set BEDROCK_API_KEY / OPENAI_API_KEY env var.\n"
                        "Create one at: https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html"
                    )
                    sys.exit(1)
                base_url = f"https://bedrock-mantle.{region}.api.aws/v1"
                self.openai_client = OpenAI(base_url=base_url, api_key=key)
                self.client = None
            else:
                self.client = boto3.client("bedrock-runtime", region_name=region)
                self.openai_client = None
        else:
            self.client = None
            self.openai_client = None

        # Shared counters for the current phase
        self._lock = threading.Lock()
        self._success_count = 0
        self._throttle_count = 0
        self._error_count = 0
        self._latencies: list[float] = []

    def _reset_counters(self):
        """Reset per-phase counters."""
        with self._lock:
            self._success_count = 0
            self._throttle_count = 0
            self._error_count = 0
            self._latencies = []

    def _invoke_model(self) -> None:
        """
        Make a single inference call and record the outcome.

        Uses the Converse API (bedrock-runtime) or Chat Completions API
        (bedrock-mantle) depending on the configured endpoint.

        Counts 503 / ThrottlingException / 429 as throttled (the signal to reduce rate).
        All other errors are counted separately.
        """
        if self.endpoint == "mantle":
            self._invoke_mantle()
        else:
            self._invoke_runtime()

    def _invoke_mantle(self) -> None:
        """Invoke via bedrock-mantle (OpenAI-compatible Chat Completions API)."""
        start = time.perf_counter()
        try:
            response = self.openai_client.chat.completions.create(
                model=self.model_id,
                messages=[{"role": "user", "content": TEST_PROMPT}],
                max_tokens=self.max_tokens,
                temperature=0.1,
            )
            latency_ms = (time.perf_counter() - start) * 1000
            with self._lock:
                self._success_count += 1
                self._latencies.append(latency_ms)

        except Exception as e:
            error_str = str(e).lower()
            # OpenAI SDK raises exceptions with status codes in the message
            if any(code in error_str for code in ("429", "503", "rate_limit", "throttl", "service_unavailable")):
                with self._lock:
                    self._throttle_count += 1
            else:
                with self._lock:
                    self._error_count += 1
                console.print(f"[yellow]Non-throttle error (mantle): {e}[/yellow]")

    def _invoke_runtime(self) -> None:
        """Invoke via bedrock-runtime (Converse API)."""
        start = time.perf_counter()
        try:
            response = self.client.converse(
                modelId=self.model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": TEST_PROMPT}],
                    }
                ],
                inferenceConfig={
                    "maxTokens": self.max_tokens,
                    "temperature": 0.1,
                },
            )
            latency_ms = (time.perf_counter() - start) * 1000
            with self._lock:
                self._success_count += 1
                self._latencies.append(latency_ms)

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            # 503 ServiceUnavailable or ThrottlingException = throttled
            if error_code in ("ThrottlingException", "ServiceUnavailableException",
                              "ModelNotReadyException"):
                with self._lock:
                    self._throttle_count += 1
            else:
                with self._lock:
                    self._error_count += 1
                console.print(f"[yellow]Non-throttle error: {error_code}[/yellow]")

        except Exception as e:
            with self._lock:
                self._error_count += 1
            console.print(f"[red]Unexpected error: {e}[/red]")

    def _run_phase(self, rpm: float, duration_seconds: float) -> PhaseResult:
        """
        Run requests at the given RPM for the specified duration.

        Uses a thread pool to maintain concurrency while spacing requests
        to achieve the target rate.
        """
        self._reset_counters()
        interval = 60.0 / rpm  # seconds between requests
        total_requests = int(rpm * (duration_seconds / 60.0))
        concurrency = max(1, min(int(rpm / 10), 50))  # scale threads with RPM

        console.print(
            f"  [cyan]Phase:[/cyan] {rpm:.0f} RPM × {duration_seconds:.0f}s "
            f"({total_requests} requests, {concurrency} threads)"
        )

        start_time = time.perf_counter()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = []
            for i in range(total_requests):
                # Budget cap: abort if total requests across all phases exceeded
                if self.max_requests and self._total_requests_all_phases >= self.max_requests:
                    console.print(
                        f"  [bold red]⛔ Budget cap reached ({self.max_requests:,} requests). "
                        f"Stopping test.[/bold red]"
                    )
                    break

                # Check if we've exceeded duration
                elapsed = time.perf_counter() - start_time
                if elapsed >= duration_seconds:
                    break

                # Schedule at the target interval
                target_time = start_time + (i * interval)
                now = time.perf_counter()
                if target_time > now:
                    time.sleep(target_time - now)

                futures.append(executor.submit(self._invoke_model))
                self._total_requests_all_phases += 1

            # Wait for all in-flight requests
            for f in as_completed(futures):
                f.result()  # propagate any unhandled exceptions

        actual_duration = time.perf_counter() - start_time
        total = self._success_count + self._throttle_count + self._error_count
        actual_rpm = (total / actual_duration) * 60 if actual_duration > 0 else 0
        error_rate = (self._throttle_count / total) if total > 0 else 0.0

        # Latency percentiles
        p50, p90, p99 = 0.0, 0.0, 0.0
        if self._latencies:
            sorted_lat = sorted(self._latencies)
            p50 = sorted_lat[int(len(sorted_lat) * 0.5)]
            p90 = sorted_lat[int(len(sorted_lat) * 0.9)]
            p99 = sorted_lat[min(int(len(sorted_lat) * 0.99), len(sorted_lat) - 1)]

        return PhaseResult(
            target_rpm=rpm,
            actual_rpm=round(actual_rpm, 1),
            duration_seconds=round(actual_duration, 1),
            total_requests=total,
            successful_requests=self._success_count,
            throttled_requests=self._throttle_count,
            other_errors=self._error_count,
            error_rate=round(error_rate, 4),
            latency_p50_ms=round(p50, 1),
            latency_p90_ms=round(p90, 1),
            latency_p99_ms=round(p99, 1),
            steady_state=(error_rate < self.error_threshold),
        )

    def _find_steady_state(self, start_rpm: float) -> float:
        """
        Reduce RPM by the reduction factor until errors fall below threshold.

        This implements steps 1-3 of the ramp-up procedure:
        - Start at target
        - If 503s, reduce by 50%
        - Repeat until steady state
        """
        current_rpm = start_rpm
        min_rpm = 10  # floor to avoid infinite reduction
        probe_duration = 60  # 1-minute probes to find steady state

        console.print(f"\n[bold]Phase 1: Finding steady state (starting at {current_rpm:.0f} RPM)[/bold]")

        while current_rpm >= min_rpm:
            result = self._run_phase(current_rpm, probe_duration)

            if result.steady_state:
                console.print(
                    f"  [green]✓ Steady state found at {current_rpm:.0f} RPM "
                    f"(error rate: {result.error_rate:.1%})[/green]"
                )
                return current_rpm
            else:
                console.print(
                    f"  [yellow]✗ Too many 503s at {current_rpm:.0f} RPM "
                    f"(error rate: {result.error_rate:.1%}) — reducing...[/yellow]"
                )
                current_rpm = max(min_rpm, current_rpm * self.reduction_factor)

        console.print(f"  [red]Could not find steady state above {min_rpm} RPM[/red]")
        return min_rpm

    def run(self) -> RampTestResult:
        """
        Execute the full ramp-up procedure.

        Returns a RampTestResult with the complete history of all phases.
        """
        result = RampTestResult(
            model_id=self.model_id,
            region=self.region,
            target_rpm=self.target_rpm,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        console.print(Panel(
            f"[bold]Amazon Bedrock Ramp Testing[/bold]\n\n"
            f"Model:       {self.model_id}\n"
            f"Region:      {self.region}\n"
            f"Endpoint:    bedrock-{self.endpoint} "
            f"({'OpenAI-compatible API' if self.endpoint == 'mantle' else 'Converse API'})\n"
            f"Target RPM:  {self.target_rpm}\n"
            f"Hold time:   {self.hold_minutes} min\n"
            f"Reduce by:   {self.reduction_factor:.0%} on 503\n"
            f"Increase by: {self.increase_factor:.0%} on steady state\n"
            f"Max requests:{f' {self.max_requests:,}' if self.max_requests else ' unlimited'}\n"
            f"Dry run:     {self.dry_run}",
            title="Configuration",
        ))

        if self.dry_run:
            self._print_dry_run_plan()
            return result

        # Step 1-3: Find steady state by reducing from target
        steady_rpm = self._find_steady_state(self.target_rpm)

        # Step 4-6: Hold and ramp up
        current_rpm = steady_rpm
        hold_seconds = self.hold_minutes * 60

        console.print(f"\n[bold]Phase 2: Ramp up from {current_rpm:.0f} → {self.target_rpm:.0f} RPM[/bold]")

        while current_rpm <= self.target_rpm:
            console.print(f"\n  [bold cyan]Holding at {current_rpm:.0f} RPM for {self.hold_minutes} min...[/bold cyan]")
            phase_result = self._run_phase(current_rpm, hold_seconds)
            result.phases.append(asdict(phase_result))

            if not phase_result.steady_state:
                # If we get throttled during hold, reduce and retry
                console.print(
                    f"  [yellow]Throttled during hold — reducing to "
                    f"{current_rpm * self.reduction_factor:.0f} RPM[/yellow]"
                )
                current_rpm *= self.reduction_factor
                continue

            console.print(
                f"  [green]✓ Stable at {current_rpm:.0f} RPM | "
                f"p50={phase_result.latency_p50_ms:.0f}ms "
                f"p90={phase_result.latency_p90_ms:.0f}ms "
                f"p99={phase_result.latency_p99_ms:.0f}ms[/green]"
            )

            # Check if we've reached or exceeded target
            if current_rpm >= self.target_rpm:
                result.success = True
                result.achieved_rpm = current_rpm
                break

            # Increase for next phase
            current_rpm = min(current_rpm * self.increase_factor, self.target_rpm)

        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.achieved_rpm = current_rpm if result.success else current_rpm / self.increase_factor

        self._print_summary(result)
        return result

    def _print_dry_run_plan(self):
        """Show the ramp plan without making any API calls."""
        console.print("\n[bold]Dry Run — Ramp Plan:[/bold]\n")

        # Simulate finding steady state
        rpm = self.target_rpm
        step = 1
        console.print("  [cyan]Phase 1: Find steady state[/cyan]")
        while rpm > 10:
            console.print(f"    Step {step}: Probe at {rpm:.0f} RPM (60s)")
            console.print(f"             If 503 rate > {self.error_threshold:.0%} → reduce to {rpm * self.reduction_factor:.0f} RPM")
            console.print(f"             If stable → begin hold phase")
            rpm *= self.reduction_factor
            step += 1
            if step > 5:
                console.print("    ...")
                break

        console.print(f"\n  [cyan]Phase 2: Ramp up (hold {self.hold_minutes} min at each level)[/cyan]")
        # Simulate ramp from a plausible steady state
        example_steady = self.target_rpm * (self.reduction_factor ** 2)  # e.g. 25% of target
        rpm = example_steady
        step = 1
        while rpm <= self.target_rpm:
            console.print(f"    Step {step}: Hold at {rpm:.0f} RPM for {self.hold_minutes} min")
            rpm = min(rpm * self.increase_factor, self.target_rpm)
            step += 1
            if rpm > self.target_rpm:
                break
        console.print(f"    Step {step}: Hold at {self.target_rpm:.0f} RPM for {self.hold_minutes} min ← [green]TARGET[/green]")

    def _print_summary(self, result: RampTestResult):
        """Print a formatted summary table."""
        console.print("\n")
        table = Table(title="Ramp Test Summary")
        table.add_column("Phase", style="cyan")
        table.add_column("Target RPM", justify="right")
        table.add_column("Actual RPM", justify="right")
        table.add_column("Success Rate", justify="right")
        table.add_column("p50 (ms)", justify="right")
        table.add_column("p90 (ms)", justify="right")
        table.add_column("Status")

        for i, phase in enumerate(result.phases, 1):
            success_rate = 1 - phase["error_rate"]
            status = "[green]✓ Steady[/green]" if phase["steady_state"] else "[yellow]⚠ Throttled[/yellow]"
            table.add_row(
                str(i),
                f"{phase['target_rpm']:.0f}",
                f"{phase['actual_rpm']:.0f}",
                f"{success_rate:.1%}",
                f"{phase['latency_p50_ms']:.0f}",
                f"{phase['latency_p90_ms']:.0f}",
                status,
            )

        console.print(table)

        if result.success:
            console.print(f"\n[bold green]✓ Successfully ramped to {result.achieved_rpm:.0f} RPM[/bold green]")
        else:
            console.print(
                f"\n[bold yellow]⚠ Reached {result.achieved_rpm:.0f} RPM "
                f"(target was {result.target_rpm:.0f} RPM)[/bold yellow]"
            )
            console.print(
                "  Consider requesting a quota increase or contact AWS support "
                "for capacity pre-provisioning."
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_total_requests(
    target_rpm: float, hold_minutes: float, reduction_factor: float, increase_factor: float
) -> int:
    """
    Estimate the upper-bound total requests for a full ramp test.

    Accounts for probe phases (finding steady state) + hold phases (ramp up).
    This is conservative (actual will often be lower if steady state is found quickly).
    """
    # Probe phase: up to 5 reductions, each 60s
    probe_requests = 0
    rpm = target_rpm
    for _ in range(5):
        probe_requests += int(rpm)  # 60s at this RPM
        rpm *= reduction_factor

    # Ramp phase: from lowest steady state up to target, hold_minutes each
    ramp_requests = 0
    steady_rpm = target_rpm * (reduction_factor ** 2)  # assume 2 reductions
    rpm = steady_rpm
    while rpm <= target_rpm:
        ramp_requests += int(rpm * hold_minutes)
        rpm *= increase_factor
        if rpm > target_rpm:
            ramp_requests += int(target_rpm * hold_minutes)
            break

    return probe_requests + ramp_requests


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Amazon Bedrock TPM/RPM Ramp Testing Tool.\n\n"
            "Implements the recommended ramp-up procedure from:\n"
            "https://docs.aws.amazon.com/bedrock/latest/userguide/scaling-throughput-best-practices.html"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID,
                        help=f"Bedrock model ID (default: {DEFAULT_MODEL_ID})")
    parser.add_argument("--region", default="us-east-1",
                        help="AWS region (default: us-east-1)")
    parser.add_argument("--target-rpm", type=float, default=500,
                        help="Target requests per minute (default: 500)")
    parser.add_argument("--hold-minutes", type=float, default=15,
                        help="Minutes to hold at each steady state (default: 15)")
    parser.add_argument("--reduction-factor", type=float, default=0.5,
                        help="Factor to reduce by on 503 errors (default: 0.5)")
    parser.add_argument("--increase-factor", type=float, default=1.5,
                        help="Factor to increase by after steady state (default: 1.5)")
    parser.add_argument("--error-threshold", type=float, default=0.05,
                        help="Error rate threshold to trigger reduction (default: 0.05)")
    parser.add_argument("--max-tokens", type=int, default=100,
                        help="Max tokens per request (default: 100)")
    parser.add_argument("--endpoint", choices=["runtime", "mantle"], default="runtime",
                        help="Bedrock endpoint: 'runtime' (Converse API) or 'mantle' "
                             "(OpenAI-compatible API) (default: runtime)")
    parser.add_argument("--api-key", default=None,
                        help="Bedrock API key for bedrock-mantle endpoint "
                             "(or set BEDROCK_API_KEY / OPENAI_API_KEY env var)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without making API calls")
    parser.add_argument("--max-requests", type=int, default=None,
                        help="Hard cap on total requests across all phases (budget safety). "
                             "Aborts the test once this limit is reached.")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the confirmation prompt (for CI/automation)")
    parser.add_argument("--output", default="results.json",
                        help="Output file for results (default: results.json)")

    args = parser.parse_args()

    tester = BedrockRampTester(
        model_id=args.model_id,
        region=args.region,
        target_rpm=args.target_rpm,
        hold_minutes=args.hold_minutes,
        reduction_factor=args.reduction_factor,
        increase_factor=args.increase_factor,
        error_threshold=args.error_threshold,
        max_tokens=args.max_tokens,
        dry_run=args.dry_run,
        endpoint=args.endpoint,
        api_key=args.api_key,
        max_requests=args.max_requests,
    )

    # Cost/confirmation gate (skip for dry runs and --yes)
    if not args.dry_run and not args.yes:
        # Estimate upper-bound requests: probe phase + ramp phases
        est_requests = _estimate_total_requests(
            args.target_rpm, args.hold_minutes, args.reduction_factor, args.increase_factor
        )
        if args.max_requests:
            est_requests = min(est_requests, args.max_requests)

        console.print(Panel(
            f"[bold yellow]⚠️  Cost Warning[/bold yellow]\n\n"
            f"This test will make real API calls to Amazon Bedrock.\n\n"
            f"  Estimated max requests:  ~{est_requests:,}\n"
            f"  Tokens per request:      ~{args.max_tokens} output + ~30 input\n"
            f"  Estimated max tokens:    ~{est_requests * (args.max_tokens + 30):,}\n"
            f"  Budget cap (--max-requests): {args.max_requests or 'unlimited'}\n\n"
            f"Pricing varies by model. Check:\n"
            f"  https://aws.amazon.com/bedrock/pricing/\n\n"
            f"Use --dry-run to preview the plan without cost.\n"
            f"Use --max-requests N to set a hard request cap.\n"
            f"Use --yes to skip this prompt.",
            title="Confirmation Required",
            border_style="yellow",
        ))
        try:
            answer = input("\nProceed? [y/N] ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            answer = ""
        if answer not in ("y", "yes"):
            console.print("[dim]Aborted.[/dim]")
            sys.exit(0)

    result = tester.run()

    # Write results to file
    if not args.dry_run:
        with open(args.output, "w") as f:
            json.dump(asdict(result), f, indent=2, default=str)
        console.print(f"\n[dim]Results written to {args.output}[/dim]")


if __name__ == "__main__":
    main()
