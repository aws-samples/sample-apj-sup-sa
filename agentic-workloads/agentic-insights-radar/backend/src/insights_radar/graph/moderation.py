# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Removing nodes people delete, including ones with more links than one request can take apart.

A delete loads every edge and bumps each neighbour's degree one UpdateItem at a time, inside the API
function's 29 s. Past ``MAX_DELETE_EDGES`` that would time out half-way, so such a node is queued
(``GRAPHDELETE`` items in the ops table) and the sweep prunes its edges a batch at a time before
deleting it: any evidence-writing module could otherwise make a visible entity undeletable through the
API by recording 501 pieces of evidence about it (B3-069).
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, iso, parse_iso
from insights_radar.core.dynamo import Query
from insights_radar.core.errors import NotFoundError
from insights_radar.graph.model import Node
from insights_radar.graph.registry import Category, EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository, from_ddb

log = logging.getLogger(__name__)

MAX_DELETE_EDGES = 500
QUEUE_PK = "GRAPHDELETE"
# The most created-entity ids one run's finalize looks at (the module runtime reports at most as many).
MAX_RUN_ORPHANS = 200


@dataclass(frozen=True, slots=True)
class Removal:
    edges_removed: int
    drafts_removed: int


class NodeRemoval:
    def __init__(
        self, graph: GraphRepository, insights: Any, s3: Any, bucket: str, ops_table: Any, clock: Clock
    ) -> None:
        self._graph = graph
        self._insights = insights
        self._s3 = s3
        self._bucket = bucket
        self._ops = ops_table
        self._clock = clock

    @classmethod
    def of(cls, ctx: Any) -> NodeRemoval:
        """From an application context (the API's or a worker's)."""
        return cls(ctx.graph, ctx.insights, ctx.s3, ctx.settings.assets_bucket, ctx.ops_table, ctx.clock)

    def _draft_key(self, node_id: str) -> str:
        return f"drafts/{node_id.split('/', 1)[1]}.md"

    def remove(self, node: Node) -> Removal:
        """Delete ``node`` with its edges, its votes and working-on marks, its markdown (a draft) and its
        drafts (an idea). The node must have at most ``MAX_DELETE_EDGES`` links."""
        # Collected before the delete takes the edges with it: a draft left behind is listed and
        # readable, and points at an idea that no longer exists. Every one of them: the default edge
        # page stopped at 200 (B3-075).
        # Flagged before anything is read: from here on no vote, mark or link (a new draft's HAS_DRAFT) can be written
        # to it, so the drafts read below are all of them and the purge below is the last one needed (code review
        # r61, r62).
        self._graph.mark_deleting(node.id)
        drafts = (
            [
                e.target
                # Consistent: a draft linked just before the flag must be seen (code review r63).
                for e in self._graph.edges(
                    node.id, direction="out", edge=EdgeType.HAS_DRAFT, limit=None, consistent=True
                )
            ]
            if node.type is NodeType.CONTENT_IDEA
            else []
        )
        # The drafts go first: a failure part way leaves the idea and the rest of its drafts in place, so the delete
        # can simply be asked again (code review r57). Votes and markdown go even when a draft node was already gone.
        removed = gone = 0
        for draft_id in drafts:
            with contextlib.suppress(NotFoundError):  # a dangling link: nothing to flag
                self._graph.mark_deleting(draft_id)  # no new vote on it from here on (code review r61)
            self._forget_draft(draft_id)
            try:
                removed += self._graph.delete(draft_id)
                gone += 1
            except NotFoundError:  # a dangling link to a draft already gone; the idea's delete takes the link
                pass
        # The node's own votes, working-on marks (with emails) and markdown go before the node too: once the node is
        # gone a retry finds nothing to remove, so a failure after it would leave them for good (code review r58).
        self._insights.purge(node.id)
        if node.type is NodeType.CONTENT_DRAFT:  # the markdown lives in S3 under the id-derived key
            self._s3.delete_object(Bucket=self._bucket, Key=self._draft_key(node.id))
        # Votes and working-on marks are written only while the node exists and is not flagged (put_while_exists),
        # so the purge above is the last one needed (code review r59, r60, r61).
        removed += self._graph.delete(node.id)
        return Removal(removed, gone)

    def _forget_draft(self, draft_id: str) -> None:
        self._insights.purge(draft_id)
        self._s3.delete_object(Bucket=self._bucket, Key=self._draft_key(draft_id))

    def queue(self, node: Node, actor: str) -> None:
        # Flagged now, not at the final remove: while the sweep takes it apart, no new link, vote or mark (with an
        # email) is written to a node already on its way out (code review r63).
        self._graph.mark_deleting(node.id)
        self._ops.put_item(
            Item={
                "pk": QUEUE_PK,
                "sk": node.id,
                "kind": "graph_delete",
                "requested_by": actor,
                "requested_at": iso(self._clock.now()),
                "type": node.type.value,
                "title": node.title[:200],
            }
        )

    def sweep(self, audit: Any = None) -> int:
        """Take one batch of edges off each queued node, and delete the ones now small enough. Returns
        how many nodes were deleted. One node that cannot be swept is logged and left queued for the next
        sweep; it never holds up the rest of the queue (code review r56, r58)."""
        done = 0
        for item in Query.all_items(
            self._ops, KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": QUEUE_PK}
        ):
            try:
                done += self._sweep_one(item, audit)
            except Exception:  # any failure: this node stays queued, the others go on
                log.exception("could not sweep queued node %s", item.get("sk"))
        return done

    def _sweep_one(self, item: dict[str, Any], audit: Any) -> int:
        node_id = str(item["sk"])
        try:
            node = self._graph.get(node_id)
        except NotFoundError:  # gone: by an earlier sweep whose audit or queue delete failed, a merge, or by hand
            # Whatever it left behind goes too, so the queue item is the last chance to clean up (code review r60).
            if item.get("type") == NodeType.CONTENT_DRAFT.value:
                self._forget_draft(node_id)
            else:
                self._insights.purge(node_id)
            if audit is not None:
                removed = item.get("removed")
                # A deletion an earlier sweep made but could not audit is audited now, with what it removed; any other
                # gone node gets its own action, since the sweep did not delete it (code review r60, r61).
                audit.record_actor(
                    "system:sweep",
                    "",
                    "graph.delete" if isinstance(removed, dict) else "graph.delete_dequeued",
                    node_id,
                    {
                        "requested_by": item.get("requested_by"),
                        **(
                            from_ddb(removed)
                            if isinstance(removed, dict)
                            else {"type": item.get("type"), "title": item.get("title")}
                        ),
                        "queued": True,
                    },
                )
            self._ops.delete_item(Key={"pk": QUEUE_PK, "sk": node_id})
            return 0
        if node.degree > MAX_DELETE_EDGES:
            if node.type is NodeType.CONTENT_IDEA:
                self._take_drafts(node_id, item, audit)
            else:
                self._graph.prune(node_id, MAX_DELETE_EDGES)
            node = self._graph.get(node_id)
        if node.degree > MAX_DELETE_EDGES:
            return 0  # the next sweep takes the next batch
        removal = self.remove(node)
        done = {
            "type": node.type.value,
            "title": node.title[:200],
            "edges_removed": removal.edges_removed,
            "drafts_removed": removal.drafts_removed,
        }
        # Kept on the queue item until the audit is written: if the audit fails, the next sweep finds the node gone
        # and audits this deletion from it (code review r61).
        try:
            self._ops.update_item(
                Key={"pk": QUEUE_PK, "sk": node_id},
                UpdateExpression="SET removed = :d",
                ConditionExpression="attribute_exists(pk)",  # never recreate a queue item cleared meanwhile (r62)
                ExpressionAttributeValues={":d": done},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise  # dequeued by someone else meanwhile: the deletion is still audited below
        # Audited before the queue item goes: a failed audit write leaves the item queued (code review r59).
        if audit is not None:
            audit.record_actor(
                "system:sweep",
                "",
                "graph.delete",
                node_id,
                {"requested_by": item.get("requested_by"), **done, "queued": True},
            )
        self._ops.delete_item(Key={"pk": QUEUE_PK, "sk": node_id})
        return 1

    def _take_drafts(self, node_id: str, item: dict[str, Any], audit: Any) -> None:
        """One batch of a large idea: its drafts go with it, not left behind by a prune that took their HAS_DRAFT
        links. When no draft is left, or only links to gone drafts whose cleanup keeps failing, its other links
        are pruned like any node's (code review r53, r58), so the idea still comes down to a size remove takes."""
        drafts = list(self._graph.edges(node_id, direction="out", edge=EdgeType.HAS_DRAFT, limit=MAX_DELETE_EDGES))
        stuck: set[str] = set()
        for e in drafts:
            try:
                draft = self._graph.get(e.target)
            except NotFoundError:  # a dangling link: no reason to stop the sweep
                try:
                    self._forget_draft(e.target)  # its votes and markdown, which outlive the node otherwise
                except Exception:  # one bad draft must not hold up every queued delete (code review r56)
                    log.exception("could not forget gone draft %s", e.target)
                    stuck.add(e.target)  # its link stays, so the next sweep tries again (code review r57)
                    continue
                self._graph.unlink(e.source, e.type, e.target)
                continue
            self.remove(draft)
            if audit is not None:  # each draft that goes ahead of its idea is audited (code review r54)
                audit.record_actor(
                    "system:sweep",
                    "",
                    "graph.delete",
                    draft.id,
                    {
                        "requested_by": item.get("requested_by"),
                        "type": draft.type.value,
                        "title": draft.title[:200],
                        "with_idea": node_id,
                    },
                )
        if stuck and len(stuck) == len(drafts):  # only gone drafts whose cleanup keeps failing are left
            if len(drafts) < MAX_DELETE_EDGES:  # all of its draft links were read: prune around them (code review r60)
                self._graph.prune(node_id, MAX_DELETE_EDGES, keep=EdgeType.HAS_DRAFT, kept=len(stuck))
            else:  # a full page of stuck drafts: nothing safe to prune this sweep
                log.warning("queued idea %s: %d gone drafts cannot be cleaned up", node_id, len(stuck))
        elif not drafts:  # no drafts left: its other links are pruned like any node's (code review r53), except a
            # draft linked since the read, which must not lose its idea (code review r61)
            self._graph.prune(node_id, MAX_DELETE_EDGES, keep=EdgeType.HAS_DRAFT)


class RunOrphans:
    """Entities a module run created and left with no link, removed when a run that ended early is finalized
    (Daily check 2026-11-10). A run stopped by its budget, cancelled or failed between ``kg_upsert_entity``
    and the evidence or link that would connect the entity left a node with no edges, which is what the admin
    "Graph hygiene" view lists as an orphan (``GraphRepository.entity_hygiene``). A run that finished keeps what
    it created (``ModuleService._remove_orphans``).

    Only a node that is still there, is an entity, was written by this module (``created_by`` is the module's
    actor), was created no earlier than the run started and still has no link in or out is deleted, with its
    ``K#<type>#<name_key>`` name key (so the name can be created again), by ``GraphRepository.delete_if_unlinked``:
    one conditional transaction, so a link written at the same moment wins over the delete. One gap is left: another
    module's ``kg_upsert_entity`` of the same name returns this entity without linking it, and if that module's
    evidence is written before the delete and linked after it, the evidence is left unlinked instead.
    A new entity has no
    votes, marks or drafts to remove, and the stream takes it out of the search index. Every failure is logged
    and skipped: the cleanup never fails the finalize."""

    def __init__(self, graph: GraphRepository, audit: Any) -> None:
        self._graph = graph
        self._audit = audit

    @classmethod
    def of(cls, ctx: Any) -> RunOrphans:
        return cls(ctx.graph, ctx.audit)

    @staticmethod
    def removable(node: Node, actor: str, since: datetime) -> bool:
        if node.category is not Category.ENTITY or node.degree != 0 or node.created_by != actor:
            return False
        try:
            return parse_iso(node.created_at) >= since
        except ValueError:  # no creation time to compare: not provably this run's, so kept
            return False

    def remove(self, module: str, actor: str, run_id: str, since: str, node_ids: Sequence[str]) -> list[str]:
        """Delete the run's own unlinked entities among ``node_ids``; returns the ids deleted."""
        start = parse_iso(since)
        removed: list[dict[str, str]] = []
        kept = 0
        for node_id in list(dict.fromkeys(node_ids))[:MAX_RUN_ORPHANS]:
            try:
                node = self._graph.get(node_id)
                if not self.removable(node, actor, start):
                    kept += 1
                    continue
                if not self._graph.delete_if_unlinked(node):  # linked since the read: it is in use
                    kept += 1
                    continue
            except NotFoundError:
                continue  # deleted or merged away already
            except Exception:
                log.exception("could not remove run %s's unlinked entity %s", run_id, node_id)
                continue
            removed.append({"id": node.id, "type": node.type.value, "title": node.title[:200]})
        if removed:
            log.info(
                json.dumps(
                    {
                        "event": "run_orphans_removed",
                        "module": module,
                        "run_id": run_id,
                        "removed": [r["id"] for r in removed],
                        "kept": kept,
                    }
                )
            )
            try:
                self._audit.record_actor(
                    "system:finalize",
                    "",
                    "graph.delete_run_orphans",
                    f"{module}/{run_id}",
                    {"module": module, "run_id": run_id, "removed": removed, "kept": kept},
                )
            except Exception:
                log.exception("audit write failed for run %s's removed entities", run_id)
        return [r["id"] for r in removed]
