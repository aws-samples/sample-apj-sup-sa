# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""DynamoDB Streams -> vector index projector (replaces the internal radar's sync-os Lambda).

The stream is the outbox: every node write produces a record, so no separate change log is
needed. Only node items (``sk == META``) are indexed; edges and name keys are skipped. A
MODIFY whose searchable text is unchanged (for example a degree counter bump) is skipped too,
which keeps embedding cost proportional to real content changes. Failures are reported per
record so Lambda retries only the failed ones (``ReportBatchItemFailures``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from boto3.dynamodb.types import TypeDeserializer

from insights_radar.core.errors import NotFoundError
from insights_radar.graph.model import Node
from insights_radar.graph.registry import NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.search import Embedder, VectorIndex

log = logging.getLogger(__name__)
_DE = TypeDeserializer()


def _image(record: dict[str, Any], which: str) -> dict[str, Any] | None:
    raw = record.get("dynamodb", {}).get(which)
    if not raw:
        return None
    return {k: _DE.deserialize(v) for k, v in raw.items()}


@dataclass
class IndexReport:
    indexed: int = 0
    deleted: int = 0
    skipped: int = 0
    failed_ids: list[str] = field(default_factory=list)

    def batch_response(self) -> dict[str, Any]:
        return {"batchItemFailures": [{"itemIdentifier": i} for i in self.failed_ids]}


class StreamIndexer:
    def __init__(self, index: VectorIndex, embedder: Embedder, on_failure: Callable[[str], None] | None = None) -> None:
        self._index = index
        self._embedder = embedder
        # Told the node id of every record that failed: the DLQ keeps only stream positions, for 14
        # days, so a failed node stayed out of search for good (CTR HC-074).
        self._on_failure = on_failure

    def index_node(self, node: Node) -> bool:
        """Put (or, for a draft, remove) one node's vector; whether it was indexed."""
        if node.type is NodeType.CONTENT_DRAFT:
            self._index.delete([node.id])
            return False
        self._index.put(node.id, self._embedder.embed(node.search_text), self.metadata(node))
        return True

    @staticmethod
    def metadata(node: Node) -> dict[str, Any]:
        # Only what queries filter on: updated_at and title made every attribute-only edit re-embed text
        # that had not changed (CTR HC-058).
        meta: dict[str, Any] = {"type": node.type.value}
        if node.insight_kind:
            meta["insight_kind"] = node.insight_kind
        return meta

    @staticmethod
    def _is_node(item: dict[str, Any] | None) -> bool:
        return bool(item) and item is not None and item.get("sk") == "META" and item.get("kind") == "node"

    def handle(self, event: dict[str, Any]) -> IndexReport:
        report = IndexReport()
        for record in event.get("Records", []):
            seq = record.get("dynamodb", {}).get("SequenceNumber", "")
            try:
                self._handle_record(record, report)
            except Exception:
                log.exception("index failure for sequence %s", seq)
                report.failed_ids.append(seq)
                failed = _image(record, "NewImage") or {}
                if self._on_failure is not None and self._is_node(failed):
                    try:
                        self._on_failure(str(failed["id"]))
                    except Exception:  # the DLQ still has the record
                        log.exception("could not record the failed node for sequence %s", seq)
        return report

    def _handle_record(self, record: dict[str, Any], report: IndexReport) -> None:
        name = record.get("eventName")
        new, old = _image(record, "NewImage"), _image(record, "OldImage")
        if name == "REMOVE":
            if self._is_node(old) and old is not None:
                self._index.delete([str(old["id"])])
                report.deleted += 1
            else:
                report.skipped += 1
            return
        if not self._is_node(new) or new is None:
            report.skipped += 1
            return
        node = GraphRepository._node_from_item(new)
        if node.type is NodeType.CONTENT_DRAFT:
            # Drafts are not searched (CTR HC-032); one indexed before is removed on its next write.
            self.index_node(node)
            report.skipped += 1
            return
        if name == "MODIFY" and self._is_node(old) and old is not None:
            before = GraphRepository._node_from_item(old)
            if before.search_text == node.search_text and self.metadata(before) == self.metadata(node):
                report.skipped += 1
                return
        self.index_node(node)
        report.indexed += 1


class Reindexer:
    """Put graph nodes back in the vector index, idempotently (CTR HC-106, HC-074): the nodes the stream
    indexer recorded as failed, or every node, a page at a time with a cursor to resume from."""

    FAILED_PK = "INDEXFAIL"

    def __init__(self, graph_table: Any, ops_table: Any, repo: GraphRepository, indexer: StreamIndexer) -> None:
        self._graph = graph_table
        self._ops = ops_table
        self._repo = repo
        self._indexer = indexer

    def record_failure(self, node_id: str) -> None:
        self._ops.put_item(Item={"pk": self.FAILED_PK, "sk": node_id, "kind": "index_failure"})

    def failed(self) -> dict[str, Any]:
        res = self._ops.query(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": self.FAILED_PK})
        indexed, failed = 0, []
        for item in res.get("Items", []):
            node_id = str(item["sk"])
            try:
                node = self._repo.get(node_id)
                indexed += self._indexer.index_node(node)
            except NotFoundError:
                pass  # deleted since: nothing to index
            except Exception:
                log.exception("reindex failed for %s", node_id)
                failed.append(node_id)
                continue
            self._ops.delete_item(Key={"pk": self.FAILED_PK, "sk": node_id})
        return {"indexed": indexed, "failed": failed, "more": bool(res.get("LastEvaluatedKey"))}

    def all(self, cursor: dict[str, Any] | None = None, limit: int = 500) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "FilterExpression": "sk = :m AND kind = :k",
            "ExpressionAttributeValues": {":m": "META", ":k": "node"},
            "Limit": limit,
        }
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        res = self._graph.scan(**kwargs)
        indexed, failed = 0, []
        for item in res.get("Items", []):
            node = GraphRepository._node_from_item(item)
            try:
                indexed += self._indexer.index_node(node)
            except Exception:
                log.exception("reindex failed for %s", node.id)
                failed.append(node.id)
                self.record_failure(node.id)
        return {"indexed": indexed, "failed": failed, "cursor": res.get("LastEvaluatedKey")}
