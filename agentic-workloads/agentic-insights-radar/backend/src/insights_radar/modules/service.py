# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Module run orchestration: the sweep, on-demand runs, and run finalisation.

Run flow: ``start`` records a QUEUED run, issues a run token scoped to the module's tools and
starts the Step Functions execution. The state machine's invoke step calls AgentCore with a task
token; the runtime reports back; ``finalize`` stamps the terminal state and charges the module's
budget from the usage the runtime measured.

Gates, all checked before any model call: kill switch on, module enabled (scheduled runs only),
budget not closed, concurrency below ``max_concurrency``. The sweep additionally claims the
dispatch slot atomically so overlapping sweeps cannot double-dispatch.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock, iso
from insights_radar.core.errors import (
    BudgetExhaustedError,
    ConflictError,
    GenAiDisabledError,
    NotFoundError,
    ValidationError,
)
from insights_radar.core.tokens import BudgetSnapshot, BudgetState, TokenUsage, TokenWeights
from insights_radar.identity.run_token import RunTokenService
from insights_radar.modules.catalog import ModelCatalog
from insights_radar.modules.model import ModuleDefinition, ModuleTemplate, module_actor
from insights_radar.modules.repository import ModuleRepository, ModuleRun, Progress, RunStatus

log = logging.getLogger(__name__)
RUN_TTL_SECONDS = 8 * 3600
# How much earlier than its interval a module may be dispatched (sweeps run every 5 minutes).
DISPATCH_SLACK_SECONDS = 60
# The most runs the scheduler keeps going at once across every module (CTR HC-039): each run holds a
# budget slot and model quota, and twenty modules falling due together used to start twenty runs.
MAX_LIVE_RUNS = 8
# The order the scheduler starts modules in: what a stage reads is what the stages before it write, so
# Scout goes first, and a tick that starts a stage leaves the later ones for the next tick, when the
# earlier stage's output exists (CTR HC-045, HC-057).
STAGE = {
    ModuleTemplate.SCOUT: 0,
    ModuleTemplate.SIGNAL: 1,
    ModuleTemplate.CUSTOM: 1,
    ModuleTemplate.TREND: 2,
    ModuleTemplate.GAP: 3,
    ModuleTemplate.CONTENT: 4,
    ModuleTemplate.CURATOR: 5,
    ModuleTemplate.DRAFT: 6,
}
GENAI_DISABLED_MESSAGE = (
    "AI features are paused by your administrator. Radars, search, the knowledge graph and exports still work."
)


class RunLauncher(Protocol):
    def launch(self, run: ModuleRun, payload: dict[str, Any]) -> str: ...

    def stop(self, execution_arn: str, cause: str) -> None: ...


class OrphanCleaner(Protocol):
    """Removes the entities a finished run created and left unlinked (``graph.moderation.RunOrphans``)."""

    def remove(self, module: str, actor: str, run_id: str, since: str, node_ids: Sequence[str]) -> list[str]: ...


class StepFunctionsLauncher:
    def __init__(self, sfn_client: Any, state_machine_arn: str) -> None:
        self._sfn = sfn_client
        self._arn = state_machine_arn

    def launch(self, run: ModuleRun, payload: dict[str, Any]) -> str:
        res = self._sfn.start_execution(
            stateMachineArn=self._arn, name=f"{run.module}-{run.run_id}"[:80], input=json.dumps(payload)
        )
        return str(res["executionArn"])

    def stop(self, execution_arn: str, cause: str) -> None:
        """Stop a run's execution; one that has already ended (or was never there) is not an error."""
        try:
            self._sfn.stop_execution(executionArn=execution_arn, cause=cause[:300])
        except ClientError as err:
            if err.response["Error"]["Code"] not in ("ExecutionDoesNotExist", "ExecutionNotFound"):
                raise


@dataclass(frozen=True, slots=True)
class SweepDecision:
    module: str
    dispatched: bool
    reason: str
    run_id: str | None = None


class ModuleService:
    def __init__(
        self,
        repo: ModuleRepository,
        tokens: RunTokenService,
        launcher: RunLauncher,
        clock: Clock | None = None,
        max_live_runs: int = MAX_LIVE_RUNS,
        orphans: OrphanCleaner | None = None,
    ) -> None:
        self._max_live_runs = max_live_runs
        self._orphans = orphans
        self._repo = repo
        self._tokens = tokens
        self._launcher = launcher
        self._clock = clock or SystemClock()

    def reset_progress(self, slug: str, value: datetime | None) -> Progress:
        """An administrator moves a module's cursor (B3-061), with two guards the run path has of its own: a
        cursor in the future would make the module read nothing until that date, and runs only ever move
        it forward, so nothing would undo it; and a run in progress would overwrite the reset with its next
        progress update while the admin is told it worked."""
        now = self._clock.now()
        if value is not None and value > now:
            raise ValidationError("last_processed_at must not be in the future", {"now": iso(now)})
        stale = iso(now - timedelta(seconds=RUN_TTL_SECONDS))
        if self._repo.running_count(slug, stale):
            raise ConflictError(f"module {slug} has a run in progress; reset its reading position after it ends")
        return self._repo.reset_progress(slug, None if value is None else iso(value))

    def ensure_genai(self) -> None:
        if not self._repo.andon().genai_enabled:
            raise GenAiDisabledError(GENAI_DISABLED_MESSAGE)

    def _check_gates(self, module: ModuleDefinition) -> BudgetSnapshot:
        self.ensure_genai()
        # Concurrency first: a running run's hold makes the budget look spent, and "already running"
        # is the accurate reason to refuse.
        stale = iso(self._clock.now() - timedelta(seconds=RUN_TTL_SECONDS))
        if self._repo.running_count(module.slug, stale) >= module.max_concurrency:
            raise ConflictError(f"module {module.slug} already has {module.max_concurrency} run(s) in progress")
        snap = self._repo.budget(module.slug, module.daily_token_budget)
        if snap.state is BudgetState.CLOSED:
            raise BudgetExhaustedError(
                f"module {module.slug} has used its daily token budget",
                {"consumed": snap.consumed, "limit": snap.limit},
            )
        if snap.state is BudgetState.HALF_OPEN:
            # At 90% a run is told to finish and stop on its first budget check, so it paid its setup and
            # the model's first turns for nothing (CTR HC-043).
            raise BudgetExhaustedError(
                f"module {module.slug} has used 90% or more of its daily token budget; it runs again tomorrow",
                {"consumed": snap.consumed, "limit": snap.limit, "state": snap.state.value},
            )
        return snap

    def _reserve(self, module: ModuleDefinition, snap: BudgetSnapshot, trigger: str = "manual") -> tuple[int, str]:
        """Hold this run's share of what is left, atomically: concurrent runs each hold their own
        slice instead of all being allowed the whole remainder, so together they stay within the day.

        A scheduled run also leaves room for the day's later slots: it holds what is left divided by the slots
        still to come before UTC midnight, this one included. Without that, an early run could spend the whole
        day and the later slots were cut off part way or never started (Daily check 2026-11-10: Scout spent
        about 2M a run, six runs a day, on an 8M day). A run started by hand may still use the whole remainder."""
        hold = snap.remaining // max(1, module.max_concurrency)
        if trigger == "schedule" and module.interval_minutes > 0:
            hold = min(hold, snap.remaining // self.slots_left_today(module.interval_minutes))
        hold = max(1, hold)
        day = self._repo.reserve(module.slug, hold, module.daily_token_budget)
        if day is None:
            raise BudgetExhaustedError(f"module {module.slug} has used its daily token budget")
        return hold, day

    def slots_left_today(self, interval_minutes: int) -> int:
        """How many runs a module on this interval still has before UTC midnight (the budget day), this one
        included: at least 1."""
        now = self._clock.now()
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        minutes = (midnight - now).total_seconds() / 60
        return max(1, -(-int(minutes) // max(1, interval_minutes)))

    def start(
        self, slug: str, trigger: str, actor: str, target_id: str | None = None, extra: dict[str, Any] | None = None
    ) -> ModuleRun:
        module = self._repo.get(slug)
        if trigger == "schedule" and not module.enabled:
            raise ValidationError(f"module {slug} is disabled")
        if module.interval_minutes == 0 and trigger == "schedule":
            raise ValidationError(f"module {slug} runs on demand only")
        refusal = module.run_refusal
        if refusal:
            # The validator refuses this on save, but a definition stored before the rule (or written
            # straight to the table) must not run either.
            raise ValidationError(f"module {slug} cannot run: {refusal}")
        gates = self._check_gates(module)  # the kill switch and the budget answer first
        if module.template is ModuleTemplate.DRAFT and not target_id:
            # A Drafts run writes one draft for one content idea. Without one it held budget and called the
            # model, and draft_save then refused whatever it wrote (CTR H-026).
            raise ValidationError("a Drafts run needs a content idea: start it with Write a draft on the idea")
        hold, day = self._reserve(module, gates, trigger)
        now = iso(self._clock.now())
        run = ModuleRun(
            module=slug,
            run_id=self._repo.new_run_id(),
            status=RunStatus.QUEUED,
            trigger=trigger,
            started_at=now,
            requested_by=actor,
            target_id=target_id,
            budget_hold=hold,
            hold_day=day,
            tier=ModelCatalog.get(module.model_key).tier,
        )
        stored = False
        try:
            token = self._tokens.issue(slug, run.run_id, module.tools, RUN_TTL_SECONDS, module.product_id)
            self._repo.put_run(run)
            stored = True
            self._repo.begin_progress_slot(slug)
        except Exception as err:
            if stored:  # a QUEUED record with no execution would count as live for RUN_TTL_SECONDS
                self._repo.put_run(self._not_started(run, err))
            self._repo.release(slug, hold, day)  # nothing ran: give the reserved budget back
            raise
        payload = {
            # The slug only: the definition can hold MCP server addresses that embed a credential, and this
            # payload is kept in the Step Functions execution history. The runtime reads the definition
            # through run_definition.
            "module": {"slug": module.slug},
            "run_id": run.run_id,
            "run_token": token,
            "trigger": trigger,
            "target_id": target_id,
            "extra": extra or {},
            "seed": int(self._clock.now().timestamp()),
        }
        try:
            arn = self._launcher.launch(run, payload)
        except Exception as err:
            self._repo.put_run(self._not_started(run, err))
            self._repo.release(slug, hold, day)
            raise
        started = replace(run, status=RunStatus.RUNNING, execution_arn=arn)
        if not self._repo.mark_running(started):  # already finalized: keep its terminal record
            return self._repo.get_run(slug, run.run_id)
        return started

    def cancel(self, slug: str, run_id: str, actor: str) -> ModuleRun:
        """Stop a queued or running run (CTR HC-024/027, HCB-054): its execution is stopped and the run is
        finalized as cancelled. Its tools refuse once it has ended, so an agent still mid-turn cannot
        write after this. Usage it did not report keeps the budget hold charged, as for any run that
        may have called the model."""
        run = self._repo.get_run(slug, run_id)
        if run.status.terminal:
            raise ConflictError(f"run {run_id} has already finished ({run.status.value})", {"status": run.status.value})
        if run.execution_arn:
            self._launcher.stop(run.execution_arn, f"cancelled by {actor}")
        return self.finalize(slug, run_id, RunStatus.CANCELLED, TokenUsage(), summary=f"Cancelled by {actor}.")

    def _not_started(self, run: ModuleRun, err: Exception) -> ModuleRun:
        return replace(
            run,
            status=RunStatus.FAILED,
            finished_at=iso(self._clock.now()),
            error=f"could not start run: {type(err).__name__}",
        )

    @staticmethod
    def _interval(module: ModuleDefinition) -> timedelta:
        """The interval, less DISPATCH_SLACK_SECONDS: a sweep reaching the module a few seconds earlier
        than last time (EventBridge jitter, other modules dispatched first) skipped it for a whole
        sweep, so a 360-minute module ran every 365 (CTR HC-044)."""
        return timedelta(minutes=module.interval_minutes) - timedelta(seconds=DISPATCH_SLACK_SECONDS)

    def due(self, module: ModuleDefinition) -> tuple[bool, str]:
        if not module.enabled:
            return False, "disabled"
        if module.interval_minutes == 0:
            return False, "on-demand only"
        last = self._repo.progress(module.slug).last_dispatched_at
        if last is not None:
            not_before = iso(self._clock.now() - self._interval(module))
            if last > not_before:
                return False, "interval not elapsed"
        return True, "due"

    def sweep(self) -> list[SweepDecision]:
        try:
            self.ensure_genai()
        except GenAiDisabledError:
            return [SweepDecision("*", False, "genai disabled")]
        decisions: list[SweepDecision] = []
        live = self._repo.live_runs(iso(self._clock.now() - timedelta(seconds=RUN_TTL_SECONDS)))
        started_stage: int | None = None
        for module in sorted(self._repo.list_all(), key=lambda m: (STAGE[m.template], m.slug)):
            ok, reason = self.due(module)
            if not ok:
                decisions.append(SweepDecision(module.slug, False, reason))
                continue
            stage = STAGE[module.template]
            if started_stage is not None and stage > started_stage:
                decisions.append(SweepDecision(module.slug, False, "waiting for an earlier stage"))
                continue
            if live >= self._max_live_runs:
                decisions.append(SweepDecision(module.slug, False, "deployment run cap reached"))
                continue
            now = self._clock.now()
            previous = self._repo.progress(module.slug).last_dispatched_at
            if not self._repo.claim_dispatch(module.slug, iso(now), iso(now - self._interval(module))):
                decisions.append(SweepDecision(module.slug, False, "claimed by another sweep"))
                continue
            try:
                run = self.start(module.slug, "schedule", "system:scheduler")
                decisions.append(SweepDecision(module.slug, True, "dispatched", run.run_id))
                live += 1
                started_stage = stage
            except (BudgetExhaustedError, ConflictError, ValidationError) as err:
                # Nothing was dispatched: give the slot back so the next sweep tries again.
                self._repo.release_dispatch(module.slug, iso(now), previous)
                decisions.append(SweepDecision(module.slug, False, err.code))
            except Exception:
                # Also a failed dispatch (KMS or Step Functions throttling): the claim has to go back
                # here too, or the module waits a whole interval (six hours by default) for nothing.
                self._repo.release_dispatch(module.slug, iso(now), previous)
                log.exception("dispatch failed for %s", module.slug)
                decisions.append(SweepDecision(module.slug, False, "error"))
        return decisions

    def run_tier(self, run: ModuleRun) -> str:
        """The model tier a run is charged at: the one it started on. A run recorded before the tier was
        stored falls back to its module's current model, and to the balanced tier when the module was
        deleted while the run was in flight: finalize used to raise NotFoundError there, so Step
        Functions retried and failed, and the run stayed ``running`` for 180 days (CR2-08)."""
        if run.tier:
            return run.tier
        try:
            return ModelCatalog.get(self._repo.get(run.module).model_key).tier
        except NotFoundError:
            return "balanced"

    def finalize(
        self,
        slug: str,
        run_id: str,
        status: RunStatus,
        usage: TokenUsage,
        summary: str = "",
        error: str | None = None,
        nodes_written: int = 0,
        not_started: bool = False,
        no_model_calls: bool = False,
        created_entities: Sequence[str] = (),
    ) -> ModuleRun:
        """``not_started`` is the invoke step's proof that nothing ran (a CANCELLED with no usage that
        gives the whole hold back). ``no_model_calls`` is the runtime's own proof that the run made no model
        call (the fast path: no source had new items), so a report without usage costs nothing; a run that
        booked its whole hold for doing nothing stopped Scout for the rest of the day. Every other terminal
        report without usage keeps the hold charged.

        ``created_entities`` are the ids of the entities the run created, as its runtime reported them: once the
        run is finished, the ones it left with no link are removed (``OrphanCleaner``) if it ended early."""
        if not status.terminal:
            raise ValidationError("finalize needs a terminal status")
        run = self._repo.get_run(slug, run_id)
        if run.status.terminal:
            # Idempotent: Step Functions may retry the finalize step. A retry still runs the cleanup, which an
            # earlier attempt may have died before (it is idempotent too; code review r53), unless this report says
            # the run succeeded: a run cancelled after it finished keeps what it made (code review r54).
            if status is not RunStatus.SUCCEEDED:
                self._remove_orphans(run, created_entities)
            return run
        # A run that reported nothing (it crashed, its task timed out, or the runtime lost the count) may
        # still have spent up to its hold, so the hold stays charged; otherwise the real usage replaces
        # the hold.
        # A succeeded run that does not prove it made no call is taken to have made some, so "no usage" from it
        # is unreported too.
        # Weighted at the module's own model tier: the same daily budget used to buy roughly thirty
        # times the dollars on the most expensive model as on the cheapest.
        weights = TokenWeights.for_tier(self.run_tier(run))
        # Any terminal status counts: a budget_exhausted or cancelled run may have spent (B3-025). Free only with
        # proof that nothing ran: the invoke step's not-started cancel, or the runtime's count of 0 model calls
        # (any status: GenAI paused, the budget closed, a failure before the first call; code review r29).
        unreported = not usage.raw_total and not (not_started and status is RunStatus.CANCELLED) and not no_model_calls
        done = replace(
            run,
            status=status,
            finished_at=iso(self._clock.now()),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            weighted_tokens=run.budget_hold if unreported else weights.weigh(usage),
            summary=summary[:4000],
            error=error[:2000] if error else None,
            nodes_written=max(0, nodes_written),
        )
        if not self._repo.finish_run(done, run.status):
            return self._repo.get_run(slug, run_id)  # another finalize got there first and charged it
        if usage.raw_total:
            self._repo.charge(slug, usage, weights)
        if run.budget_hold and run.hold_day and not unreported:
            self._repo.release(slug, run.budget_hold, run.hold_day)
        self._remove_orphans(done, created_entities)
        return done

    def _remove_orphans(self, run: ModuleRun, created: Sequence[str]) -> None:
        """After the run is finished (its token refuses every further write), so nothing it does can link an
        entity after the check. A failure is logged and never fails the finalize (Daily check 2026-11-10).

        Only a run that ended early (budget used up, cancelled, failed) is cleaned: that is when a run is cut off
        between creating an entity and linking it. A run that finished may have created an entity on purpose for
        a later run to use, or for a radar entry or a watch that is not a graph edge (code review r50)."""
        if not created or self._orphans is None or run.status is RunStatus.SUCCEEDED:
            return
        try:
            self._orphans.remove(run.module, module_actor(run.module), run.run_id, run.started_at, created)
        except Exception:
            log.exception("removing run %s's unlinked entities failed", run.run_id)
