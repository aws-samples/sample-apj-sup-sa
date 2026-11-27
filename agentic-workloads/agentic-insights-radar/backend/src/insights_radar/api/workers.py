# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Background Lambda entrypoints.

* ``sweep``: EventBridge every 5 minutes. Dispatches due modules, runs radar autopilot, refreshes
  knowledge-base ingestion status.
* ``invoke``: Step Functions ``.waitForTaskToken`` task. Starts the module runtime session and
  returns immediately; the runtime reports success/failure with the task token itself, so a run can
  outlive any Lambda timeout (the internal radar's D2N-014 pattern).
* ``finalize``: Step Functions task. Records the terminal run state and charges the budget.
* ``index``: DynamoDB Streams -> S3 Vectors. Embedding calls are deliberately not gated by the
  kill switch: search and the graph must keep working while GenAI is paused.
* ``seed``: CDK trigger on deploy. Creates missing built-in modules (never overwrites edits).
* ``digest_schedule``: EventBridge every 15 minutes. Queues the digest deliveries that are due.
* ``digest_send``: the digest queue, one message at a time. Builds and sends one delivery.
* ``digest_feedback``: SNS from the SES configuration set. Suppresses a delivery on a bounce or complaint.

With digest delivery switched off (the default) the three digest functions do nothing.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from collections.abc import Callable
from typing import Any, cast

from botocore.exceptions import ClientError, ParamValidationError

from insights_radar.api.context import AppContext, Settings
from insights_radar.core.dynamo import MigrationLedger
from insights_radar.core.errors import GenAiDisabledError, ServiceUnavailableError, ValidationError
from insights_radar.core.fetch import SafeFetcher
from insights_radar.core.tokens import TokenUsage
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.indexer import Reindexer, StreamIndexer
from insights_radar.graph.model import Node, node_type_of
from insights_radar.graph.moderation import MAX_RUN_ORPHANS, NodeRemoval
from insights_radar.graph.registry import CONCLUSIONS, ENTITIES, EVIDENCE, EdgeType
from insights_radar.graph.sample import SampleData
from insights_radar.graph.search import S3VectorsIndex, TitanEmbedder
from insights_radar.insights.delivery_runner import MAX_REQUEUES, Feedback, Queued, parse_message
from insights_radar.insights.delivery_transport import TransientDeliveryError
from insights_radar.modules.repository import RunStatus
from insights_radar.modules.skills import DEFAULT_SKILLS
from insights_radar.modules.watch import SourceWatcher
from insights_radar.product.crawler import DocsCrawler
from insights_radar.seed.bundle import Seeder, parse_bundle

log = logging.getLogger()
log.setLevel(logging.INFO)


@functools.cache
def _ctx() -> AppContext:
    return AppContext(Settings.from_env())


# Seconds of the seed's time kept back when a migration decides whether to read another page.
SEED_RESERVE_SECONDS = 60
# How far autopilot follows SUPPORTS edges from a cited conclusion to the evidence underneath it
# (a trend supported by signals supported by evidence is two hops).
SUPPORT_HOPS = 2


def _evidence_site(node: Node) -> str | None:
    """The site a piece of evidence corroborates from, or None when it does not count: a module's
    evidence counts only when its run really fetched the page (one injected run could otherwise record
    two "sites" it never read, SR2-02), and the sample graph's evidence has fixed, published ids and
    corroborates nothing (B3-012). Evidence a person recorded counts as it is."""
    if node.type not in EVIDENCE or not node.evidence_url or SampleData.is_sample(node.id):
        return None
    if node.created_by.startswith("module:") and not node.fetched:
        return None
    return UrlGuard.site(node.evidence_url)


def _usable_evidence(c: Any) -> Callable[[list[str]], dict[str, frozenset[str]]]:
    """The ids an unattended radar apply may rely on, mapped to the sites they rest on.

    Only evidence and conclusions count, not any node that happens to exist: an entity, or a content
    draft, satisfied the old existence check, so a prompt-injected curator could cite an unrelated
    topic. A conclusion (signal, trend, gap, insight) is a module's own word, not a site: it counts
    only through the usable evidence it SUPPORTS, so two conclusions drawn from one injected page are
    not two sites (B3-004). The sites let ``autopilot_tick`` require corroboration from more than one.
    """

    def supported(root: Node) -> frozenset[str]:
        sites: set[str] = set()
        frontier, seen = [root.id], {root.id}
        for _ in range(SUPPORT_HOPS):
            targets = [
                e.target
                for node_id in frontier
                for e in c.graph.edges(node_id, direction="out", edge=EdgeType.SUPPORTS)
                if e.target not in seen
            ]
            seen.update(targets)
            frontier = []
            for node in c.graph.get_many(targets).values():
                site = _evidence_site(node)
                if site:
                    sites.add(site)
                elif node.type in CONCLUSIONS:
                    frontier.append(node.id)
            if not frontier:
                break
        return frozenset(sites)

    def check(ids: list[str]) -> dict[str, frozenset[str]]:
        out: dict[str, frozenset[str]] = {}
        for node_id, node in c.graph.get_many(ids).items():
            site = _evidence_site(node)
            sites = frozenset({site}) if site else supported(node) if node.type in CONCLUSIONS else frozenset()
            if sites:
                out[node_id] = sites
        return out

    return check


def _autopilot(c: Any) -> dict[str, int]:
    """Autopilot, held entirely while GenAI is switched off: an admin who pulls the andon because the
    curator looks prompt-injected must not have its proposals applied on the next sweep (B3-006)."""
    paused = not c.modules.andon().genai_enabled
    result: dict[str, int] = c.radars.autopilot_tick(usable_evidence=_usable_evidence(c), audit=c.audit, paused=paused)
    return result


def sweep(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    """Each step is isolated: one broken radar, an unreachable knowledge base or a failing crawl
    request used to abort the whole sweep, so module dispatch, the status refresh and the crawls all
    stopped with it. Module dispatch is a step like the others (B3-024)."""
    c = ctx or _ctx()
    decisions = [
        {"module": d.module, "dispatched": d.dispatched, "reason": d.reason}
        for d in _step(c.module_service.sweep, "modules") or []
    ]
    autopilot = _step(_autopilot, "autopilot", c)
    refreshed = _step(_refresh_products, "products", c)
    crawls = _step(_request_crawls, "crawls", c)
    abandoned = _step(_sweep_uploads, "uploads", c)
    deleted = _step(_graph_deletes, "graph_deletes", c)
    out = {
        "modules": decisions,
        "autopilot": autopilot or {},
        "documents_marked": refreshed or 0,
        "crawls": crawls or 0,
        "abandoned_uploads": abandoned or 0,
        "nodes_deleted": deleted or 0,
    }
    log.info(
        json.dumps(
            {
                "event": "sweep",
                **{k: v for k, v in out.items() if k != "modules"},
                "dispatched": [d["module"] for d in decisions if d["dispatched"]],
            }
        )
    )
    return out


def _step(fn: Callable[..., Any], name: str, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.exception(json.dumps({"event": "sweep.step_failed", "step": name}))
        return None


def _refresh_products(c: Any) -> int:
    if c.knowledge is None:
        return 0
    status, started = c.knowledge.source_status()
    c.products.retry_owed_ingestion(status)
    refreshed = 0
    for p in c.products.list_all():
        # The sweep already has the ingestion status: passing it in saves a control-plane call per
        # product every five minutes.
        c.products.refresh_status(p.id, (status, started))
        refreshed += c.products.mark_documents_indexed(p.id, status, started)
    return refreshed


def _graph_deletes(c: Any) -> int:
    return NodeRemoval.of(c).sweep(c.audit)


def _sweep_uploads(c: Any) -> int:
    return int(c.products.sweep_expired_uploads())


def _request_crawls(c: Any) -> int:
    crawls = c.products.due_for_crawl()  # after the refresh, so finished crawls count as finished
    for product_id in crawls:
        _step(c.products.request_crawl, f"crawl:{product_id}", product_id)
    return len(crawls)


def _not_started(err: Exception) -> bool:
    """Whether an invoke error proves the run never started: the kill switch, a request botocore
    refused before sending it, or a 4xx answer from AgentCore (refused, throttled, invalid)."""
    if isinstance(err, (GenAiDisabledError, ParamValidationError)):
        return True
    if isinstance(err, ClientError):
        status = int(err.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
        return 400 <= status < 500
    return False


def invoke(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    c = ctx or _ctx()
    payload = dict(event["payload"])
    payload["task_token"] = event["task_token"]
    try:
        c.module_service.ensure_genai()
        session_id = f"{payload['module']['slug']}-{payload['run_id']}".ljust(40, "0")[:100]
        res = c.client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=event["runtime_arn"],
            runtimeSessionId=session_id,
            qualifier="DEFAULT",
            payload=json.dumps(payload).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
    except Exception as err:
        if not _not_started(err):
            # A timeout or a 5xx proves nothing: the runtime starts the run in a background thread
            # before it answers, so a slow answer can follow an accepted run. Finalizing it CANCELLED
            # released its hold while the agent kept spending (CR2-15). The step's own failure
            # handling, the heartbeat and the task timeout settle it, with the hold still charged.
            raise
        # Nothing ran: the kill switch was pulled between start and invoke, or AgentCore refused the
        # request. Finalize as cancelled with no usage so the whole hold goes back; it used to stay
        # charged as "unreported", so pulling the andon during an incident closed every queued
        # module's budget for the rest of the UTC day although nothing had run.
        _step(
            c.module_service.finalize,
            "not_started",
            payload["module"]["slug"],
            payload["run_id"],
            RunStatus.CANCELLED,
            TokenUsage(0, 0, 0, 0),
            error=f"not started: {type(err).__name__}",
            not_started=True,
        )
        if isinstance(err, GenAiDisabledError):
            # The kill switch is not a fault: fail the task with its own error, which the workflow ends
            # quietly, instead of raising (InvokeErrors) into RunErrored (RunsFailed) (CTR HC-011 follow-up).
            c.client("stepfunctions").send_task_failure(
                taskToken=event["task_token"],
                error="GenAiDisabled",
                cause="the GenAI kill switch was on; the run was not started",
            )
            return {"accepted": False, "reason": "genai_disabled"}
        raise
    body = res.get("response")
    text = body.read().decode("utf-8")[:2000] if hasattr(body, "read") else str(body)[:2000]
    log.info(
        json.dumps(
            {
                "event": "runtime_invoked",
                "module": payload["module"]["slug"],
                "run": payload["run_id"],
                "status": res.get("statusCode"),
            }
        )
    )
    return {"accepted": True, "response": text}


def finalize(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    c = ctx or _ctx()
    result = event.get("result") or {}
    error = event.get("error")
    usage_raw = result.get("usage") or {}
    counts = [
        _count(usage_raw.get(k, 0) if isinstance(usage_raw, dict) else None)
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    ]
    malformed = any(c is None for c in counts)
    # A malformed report counts as no report (the run's hold stays charged) instead of a ValueError that
    # left the run RUNNING, holding its slot and budget for hours.
    usage = TokenUsage(0, 0, 0, 0) if malformed else TokenUsage(*(c or 0 for c in counts))
    message: str | None
    if malformed:
        error = error or "the runtime reported malformed usage"
    if error:
        status = RunStatus.FAILED
        message = (
            str(error.get("Cause") or error.get("Error") or "run failed") if isinstance(error, dict) else str(error)
        )
    else:
        try:
            status = RunStatus(result.get("status", "succeeded"))
        except ValueError:
            status = RunStatus.FAILED
        raw_error = result.get("error")
        message = str(raw_error) if raw_error else None
    calls = result.get("model_calls")
    run = c.module_service.finalize(
        event["module"],
        event["run_id"],
        status if status.terminal else RunStatus.FAILED,
        usage,
        str(result.get("summary", ""))[:4000],
        message,
        _count(result.get("nodes_written", 0)) or 0,
        # The runtime's count of model calls: 0, in a clean report, proves a run with no usage spent nothing.
        no_model_calls=not error and _count(calls) == 0,  # error: the step failed, or the usage was malformed
        # The entities the run created and named in no later write; finalize removes the unlinked ones only for
        # a run that ended early. A step that failed outright (a timeout, a crash before the report) sends no list.
        created_entities=_created_entities(result.get("created_entities")),
    )
    return run.to_public()


_NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _created_entities(value: Any) -> list[str]:
    """The entity ids a runtime report lists as created by the run: strings in the node id form whose type is an
    entity, without repeats, at most ``MAX_RUN_ORPHANS``. Anything else, or no list at all (a runtime from before
    the field), is no ids: finalize then removes nothing."""
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value[: MAX_RUN_ORPHANS * 2]:
        if isinstance(item, str) and _NODE_ID_RE.fullmatch(item) and item not in ids:
            try:
                entity = node_type_of(item) in ENTITIES
            except ValidationError:  # an unknown type prefix
                continue
            if entity:
                ids.append(item)
    return ids[:MAX_RUN_ORPHANS]


def _count(value: Any) -> int | None:
    """A non-negative token or node count from the runtime's report; None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, 10**12)


def index(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    """The table stream's indexer; invoked by hand with ``{"reindex": "failed"}`` it re-indexes the nodes
    that failed, and with ``{"reindex": "all", "cursor": ...}`` every node, a page at a time (CTR HC-106)."""
    c = ctx or _ctx()
    reindexer: Reindexer | None = None
    indexer = StreamIndexer(
        S3VectorsIndex(c.client("s3vectors"), c.settings.vector_bucket, c.settings.vector_index),
        TitanEmbedder(c.client("bedrock-runtime")),
        on_failure=lambda node_id: reindexer.record_failure(node_id) if reindexer else None,
    )
    reindexer = Reindexer(c.graph_table, c.ops_table, c.graph, indexer)
    mode = event.get("reindex")
    if mode is not None:
        if mode == "failed":
            out = reindexer.failed()
        elif mode == "all":
            out = reindexer.all(event.get("cursor"))
        else:
            raise ValueError('reindex must be "failed" or "all"')
        log.info(
            json.dumps({"event": "reindex", "mode": mode, "indexed": out["indexed"], "failed": len(out["failed"])})
        )
        return out
    report = indexer.handle(event)
    log.info(
        json.dumps(
            {
                "event": "index",
                "indexed": report.indexed,
                "deleted": report.deleted,
                "skipped": report.skipped,
                "failed": len(report.failed_ids),
            }
        )
    )
    return report.batch_response()


def watch(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    """Scheduled: check every enabled source of every enabled Scout-template module for new items. Deterministic
    (no model, no GenAI budget), so it runs while GenAI is paused too: the inbox simply waits."""
    c = ctx or _ctx()
    watcher = SourceWatcher(c.watch, SafeFetcher, c.clock, current=c.modules.get_source)
    out: dict[str, Any] = {}
    for module in c.modules.list_all():
        if module.template != "scout" or not module.enabled:
            continue
        results = watcher.check_all(module.slug, c.modules.list_sources(module.slug))
        counts = {s: sum(1 for r in results if r.status == s) for s in ("ok", "empty", "error")}
        out[module.slug] = {**counts, "new": sum(r.new for r in results)}
        log.info(
            json.dumps(
                {
                    "event": "watch",
                    "module": module.slug,
                    **out[module.slug],
                    "errors": [r.to_public() for r in results if r.status != "ok"][:50],
                }
            )
        )
    return out


def crawl(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    """Async Lambda: crawl one product's documentation and index the pages."""
    c = ctx or _ctx()
    product = c.products.get(str(event["product_id"]))
    fetcher = SafeFetcher()
    try:
        report = DocsCrawler(fetcher).crawl(list(product.doc_urls))
    finally:
        fetcher.close()
    updated = c.products.store_crawl(product.id, report, product.doc_urls)
    log.info(json.dumps({"event": "crawl", "product": product.id, **report.to_public()}))
    return {"product_id": product.id, "pages": updated.web_pages, "status": updated.web_status.value}


def _time_left(context: Any) -> bool:
    """Whether the seed Lambda has time for another migration page (always, outside Lambda)."""
    remaining = context.get_remaining_time_in_millis() / 1000 if context is not None else float("inf")
    return bool(remaining > SEED_RESERVE_SECONDS)


def _apply_bundle(c: Any) -> dict[str, Any] | None:
    """The deployment's seed bundle, if the deploy named one. A bundle that does not parse fails the deploy
    (the operator sees why at once); problems with single entries are reported in the seed log and the
    rest is still applied, as with the built-in modules."""
    key = c.settings.seed_bundle_key
    if not key:
        return None
    bucket = c.settings.seed_bundle_bucket or c.settings.assets_bucket
    body = c.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        raw = json.loads(body)
    except ValueError as err:
        raise ValidationError(f"seed bundle {key} is not JSON") from err
    result = Seeder(c.ops_table, c.modules, c.profile).apply(parse_bundle(raw)).to_public()
    try:
        c.audit.record_actor(
            "system:deploy", "", "seed.applied", key, {"modules": result["modules"], "errors": len(result["errors"])}
        )
    except Exception:
        log.exception(json.dumps({"event": "audit.write_failed", "action": "seed.applied", "target": key}))
    return result


def seed(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    c = ctx or _ctx()

    def audited(action: str, slug: str, details: dict[str, Any]) -> None:
        """A deploy created a module or changed what one may do: a security-relevant edit, so it gets
        its own row as soon as it lands. Written inside the seed, not after it: a seed that stopped
        half-way used to leave the grant changes it had already made with no audit trail at all."""
        try:
            c.audit.record_actor("system:deploy", "", action, slug, details)
        except Exception:
            log.exception(json.dumps({"event": "audit.write_failed", "action": action, "target": slug}))

    report = c.modules.seed_builtins("system:deploy", audited)
    # The default writing skills (humanizer, no-ai-slop), and each built-in offered them once.
    skills_written = c.skills.seed_defaults()
    skills_offered = c.modules.offer_skills(DEFAULT_SKILLS)
    bundle = _apply_bundle(c)
    # Data migrations: idempotent re-keys that run on every deploy, because the previous release can
    # undo them after a rollback (CR3-01, P3-003). One that runs short of time stops with its place
    # recorded and resumes on the next deploy (B3-019). The tombstone split runs first: until it has,
    # a deleted private writer that only the legacy item records would not count as private (SR3-01).
    ledger = MigrationLedger(c.ops_table)

    keep_going = functools.partial(_time_left, context)
    tombstones_split = ledger.run("split_tombstone:v1", c.modules.split_tombstone, keep_going)
    rekeyed = ledger.run(
        "rekey_list_index:v1", lambda start, hook: c.graph.rekey_list_index(start=start, on_page=hook), keep_going
    )
    names_moved, name_collisions = ledger.run(
        "rekey_names:v1", lambda start, hook: c.graph.rekey_names(start=start, on_page=hook), keep_going
    )
    marks_indexed = ledger.run(
        "index_working_marks:v1",
        lambda start, hook: c.insights.index_working_marks(start=start, on_page=hook),
        keep_going,
    )
    listings = ledger.run(
        "index_created:v1", lambda start, hook: c.graph.index_created(start=start, on_page=hook), keep_going
    )
    log.info(
        json.dumps(
            {
                "event": "seed",
                "created": report.created,
                "skills_written": skills_written,
                "skills_offered": skills_offered,
                "migrated": report.migrated,
                "failed": sorted(report.failed),
                "rekeyed": rekeyed,
                "names_moved": names_moved,
                "name_collisions": name_collisions,
                "working_marks_indexed": marks_indexed,
                "tombstone_names_split": tombstones_split,
                "listings_indexed": listings,
                "warnings": report.warnings,
                "bundle": bundle,
            }
        )
    )
    if report.failed:
        # Every other built-in was still created or repaired, and each change is already audited:
        # the deploy is told at the end instead of stopping at the first module it cannot write.
        raise ServiceUnavailableError("some built-in modules could not be seeded", {"failed": report.failed})
    return {
        "created": report.created,
        "migrated": sorted(report.migrated),
        "failed": sorted(report.failed),
        "warnings": sorted(report.warnings),
    }


def digest_schedule(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    c = ctx or _ctx()
    if not c.settings.digest.any:
        return {"queued": 0, "skipped": 0, "failed": 0}
    out: dict[str, Any] = c.delivery_runner.schedule(c.client("sqs"), c.clock.now())
    log.info(json.dumps({"event": "digest.schedule", **out}))
    return out


def digest_send(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    """SQS, batch size 1, partial batch failures: a transient failure (a timeout, a 5xx) is reported back so the
    queue redelivers it; after three receives it goes to the dead-letter queue. A message naming no delivery is
    dropped. A Slack rate limit is no fault of the message, so it does not use up a receive: the send is put back
    on the queue as a new message delayed by Slack's ``Retry-After`` (up to ``MAX_REQUEUES`` times), and only if
    that fails is the message held back that long and reported as a failure."""
    c = ctx or _ctx()
    failures: list[dict[str, str]] = []
    for record in event.get("Records") or []:
        message_id = str(record.get("messageId", ""))
        if not c.settings.digest.any:
            continue
        named = parse_message(str(record.get("body", "")))
        if named is None:
            log.warning(json.dumps({"event": "digest.bad_message", "message": message_id}))
            continue
        claim = named.claim or message_id
        try:
            outcome = c.delivery_runner.deliver(
                named.subject, named.id, due=named.due, token=claim, remaining_ms=_remaining(context)
            )
        except TransientDeliveryError as err:
            log.warning(json.dumps({"event": "digest.retry", "delivery": named.id, "code": err.code}))
            if err.retry_after and _requeue(c, named, claim, err.retry_after):
                continue
            _hold_back(c, record, err.retry_after)
            failures.append({"itemIdentifier": message_id})
            continue
        except Exception:
            log.exception(json.dumps({"event": "digest.send_failed", "delivery": named.id}))
            failures.append({"itemIdentifier": message_id})
            continue
        log.info(json.dumps({"event": "digest.sent", "delivery": named.id, "outcome": outcome}))
    return {"batchItemFailures": failures}


def _remaining(context: Any) -> Callable[[], int] | None:
    """The Lambda context's clock of the run time left, when there is one."""
    clock = getattr(context, "get_remaining_time_in_millis", None)
    return cast(Callable[[], int], clock) if callable(clock) else None


def _requeue(c: Any, named: Queued, claim: str, seconds: int) -> bool:
    """Put a rate-limited send back on the queue as a new message, delayed by ``seconds`` (SQS allows up to 15
    minutes), with its receive count fresh. It keeps the first message's claim on the scheduled time, so the
    duplicate check lets it through. False once it was put back ``MAX_REQUEUES`` times, or when the queue
    refused it: the caller then reports the message as failed, as before."""
    if named.requeues >= MAX_REQUEUES or not c.settings.digest.queue_url:
        return False
    try:
        c.client("sqs").send_message(
            QueueUrl=c.settings.digest.queue_url,
            MessageBody=named.body(claim),
            DelaySeconds=max(1, min(seconds, 900)),
        )
    except Exception:
        log.exception(json.dumps({"event": "digest.requeue_failed", "delivery": named.id}))
        return False
    log.info(json.dumps({"event": "digest.requeued", "delivery": named.id, "requeues": named.requeues + 1}))
    return True


def _hold_back(c: Any, record: dict[str, Any], seconds: int | None) -> None:
    """Make a rate-limited message visible again only after Slack's Retry-After (best effort)."""
    handle = record.get("receiptHandle")
    if not seconds or not handle or not c.settings.digest.queue_url:
        return
    _step(
        c.client("sqs").change_message_visibility,
        "digest.hold_back",
        QueueUrl=c.settings.digest.queue_url,
        ReceiptHandle=handle,
        VisibilityTimeout=max(1, min(seconds, 43_200)),
    )


def digest_feedback(event: dict[str, Any], context: Any, ctx: Any = None) -> dict[str, Any]:
    c = ctx or _ctx()
    if not c.settings.digest.email:
        return {"suppressed": 0, "ignored": len(event.get("Records") or [])}
    out = Feedback(c.deliveries).handle(event)
    log.info(json.dumps({"event": "digest.feedback", **out}))
    return out
