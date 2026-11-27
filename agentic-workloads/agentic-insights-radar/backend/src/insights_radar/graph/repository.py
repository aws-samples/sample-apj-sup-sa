# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""DynamoDB-backed knowledge graph (replaces the internal radar's Neptune system of record).

Single table, adjacency-list layout:

| item | pk | sk | gsi1pk | gsi1sk |
|---|---|---|---|---|
| node | ``N#<id>`` | ``META`` | ``T#<type>`` (``T#insight#<kind>``) | ``<updated_at>#<id>`` |
| entity name key | ``K#<type>#<name_key>`` | ``KEY`` | - | - |
| edge | ``N#<from>`` | ``E#<type>#<to>`` | ``R#<to>`` | ``<type>#<from>`` |
| output listing | ``L#<type>`` (``L#insight#<kind>``) | ``<created_at>#<id>`` | - | - |

Creating an entity writes the node and its name-key item in one transaction with
``attribute_not_exists`` conditions, so two agents racing to create "Acme Motors" end up with
one node. Adding an edge writes the edge and bumps both endpoints' degree counters in one
transaction; re-adding an existing edge is a no-op. Updates use an optimistic ``version``.
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import secrets
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock, iso
from insights_radar.core.dynamo import PageHook
from insights_radar.core.errors import ConflictError, NotFoundError, ServiceUnavailableError, ValidationError
from insights_radar.core.ids import IdGenerator
from insights_radar.core.names import NameNormalizer
from insights_radar.graph.model import ALIASES_MAX, Edge, Node, NodeFactory, node_type_of
from insights_radar.graph.registry import ENTITIES, Category, EdgeType, NodeType, Registry

TRANSACTION_ATTEMPTS = 6
GET_MANY_ATTEMPTS = 8

MAX_PAGE = 100
MAX_BFS_DEPTH = 3
MAX_EDGES_PER_HOP = 200
MERGE_LOCK_SECONDS = 900  # a merge's lock lapses after this, so one that died never locks an entity for good
DEDUPE_DAYS = 7  # how long a create's idempotency key is kept, so a runtime retry is a no-op


def _to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_to_ddb(v) for v in value]
    return value


def _from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    return value


from_ddb = _from_ddb  # plain Python values from a DynamoDB item, Decimals as int or float


def encode_cursor(key: dict[str, Any] | None) -> str | None:
    if not key:
        return None
    return base64.urlsafe_b64encode(json.dumps(_from_ddb(key), sort_keys=True).encode()).decode()


TABLE_KEYS = frozenset({"pk", "sk"})
INDEX_KEYS = frozenset({"pk", "sk", "gsi1pk", "gsi1sk"})


def decode_cursor(
    cursor: str | None,
    keys: frozenset[str] | None = None,
    partition: tuple[str, str] | None = None,
    sort_prefix: tuple[str, str] | None = None,
) -> dict[str, Any] | None:
    """A client cursor back into ``ExclusiveStartKey``.

    With ``keys`` it must hold exactly those key attributes, with ``partition`` (attribute, value) it
    must stay in the queried partition, and with ``sort_prefix`` (attribute, prefix) its sort key must
    stay inside the query's ``begins_with``, so a crafted cursor can neither start a page elsewhere
    nor reach DynamoDB malformed (an int where the key is a string, or a key outside the query's
    boundaries: both a 500).
    """
    if not cursor:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, json.JSONDecodeError, RecursionError) as err:
        raise ValidationError("cursor is invalid") from err
    # Every key attribute in both tables is a string: an int reached DynamoDB as a type mismatch.
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ValidationError("cursor is invalid")
    if keys is not None and set(data) != keys:
        raise ValidationError("cursor is invalid")
    if partition is not None and data.get(partition[0]) != partition[1]:
        raise ValidationError("cursor is invalid")
    if sort_prefix is not None and not str(data.get(sort_prefix[0], "")).startswith(sort_prefix[1]):
        raise ValidationError("cursor is invalid")
    return data


@dataclass(frozen=True, slots=True)
class Page:
    items: list[Node]
    cursor: str | None


@dataclass(frozen=True, slots=True)
class Neighborhood:
    nodes: list[Node]
    edges: list[Edge]
    truncated: bool
    # One-hop paging: how many distinct neighbours the node has (before any the caller may not read are
    # dropped) and where this page starts. None for a multi-hop walk.
    total: int | None = None
    offset: int = 0


_UPDATE_SKIPS = frozenset({"pk", "sk", "in_degree", "out_degree"})


class GraphRepository:
    def __init__(
        self,
        table: Any,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._table = table
        self._client = table.meta.client
        self._clock = clock or SystemClock()
        self._ids = ids or IdGenerator(self._clock)

    # ---- item mapping --------------------------------------------------------------

    @staticmethod
    def _type_partition(node: Node) -> str:
        if node.type is NodeType.INSIGHT:
            return f"T#insight#{node.insight_kind}"
        return f"T#{node.type.value}"

    @classmethod
    def _listing_key(cls, node: Node) -> dict[str, str]:
        """The output's place in people's "newest" lists, keyed on created_at: the list index is keyed on
        updated_at (what kg_recent needs), so a one-character agent edit floated an old output to the top
        of every viewer's list (B3-052)."""
        return {"pk": "L" + cls._type_partition(node).removeprefix("T"), "sk": f"{node.created_at}#{node.id}"}

    def _listing_put(self, node: Node) -> list[dict[str, Any]]:
        if node.category is not Category.OUTPUT:
            return []
        item = {**self._listing_key(node), "kind": "listing", "node_id": node.id}
        return [{"Put": {"TableName": self._table.name, "Item": item}}]

    def _node_item(self, node: Node) -> dict[str, Any]:
        item: dict[str, Any] = {
            "pk": f"N#{node.id}",
            "sk": "META",
            "gsi1pk": self._type_partition(node),
            # updated_at, the one clock kg_recent shows a module and filters its cursor by: keyed on
            # created_at, a module that stored the updated_at it was shown skipped everything created
            # in between, and an updated trend never resurfaced (CR2-02). A whitespace-only update
            # keeps updated_at (see ``update``), so it cannot refloat an old output (B2-108).
            "gsi1sk": f"{node.updated_at}#{node.id}",
            "kind": "node",
            "id": node.id,
            "type": node.type.value,
            "title": node.title,
            "summary": node.summary,
            "description": node.description,
            "created_by": node.created_by,
            "created_at": node.created_at,
            "updated_at": node.updated_at,
            "version": node.version,
            "in_degree": node.in_degree,
            "out_degree": node.out_degree,
        }
        if node.attributes:
            item["attributes"] = node.attributes
        if node.aliases:
            item["aliases"] = list(node.aliases)
        if node.private or node.stamped:
            # Absent only on a node written before the stamp existed: an update keeps it absent, so
            # rewriting an old node never declares it public (SR2-01).
            item["private"] = node.private
        if node.fetched:
            item["fetched"] = True
        for key in ("evidence_url", "published_at", "confidence", "insight_kind", "module"):
            value = getattr(node, key)
            if value is not None:
                item[key] = value
        converted: dict[str, Any] = _to_ddb(item)
        return converted

    @staticmethod
    def _node_from_item(raw: dict[str, Any]) -> Node:
        item = _from_ddb(raw)
        return Node(
            id=item["id"],
            type=NodeType(item["type"]),
            title=item["title"],
            summary=item.get("summary", ""),
            description=item.get("description", ""),
            attributes=item.get("attributes") or {},
            aliases=tuple(item.get("aliases") or ()),
            evidence_url=item.get("evidence_url"),
            published_at=item.get("published_at"),
            confidence=item.get("confidence"),
            insight_kind=item.get("insight_kind"),
            module=item.get("module"),
            private=bool(item.get("private", False)),
            stamped="private" in item,
            fetched=bool(item.get("fetched", False)),
            created_by=item.get("created_by", ""),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
            version=int(item.get("version", 1)),
            in_degree=int(item.get("in_degree", 0)),
            out_degree=int(item.get("out_degree", 0)),
        )

    @staticmethod
    def _edge_from_item(raw: dict[str, Any]) -> Edge:
        item = _from_ddb(raw)
        return Edge(
            source=item["source"],
            type=EdgeType(item["edge_type"]),
            target=item["target"],
            created_by=item.get("created_by", ""),
            created_at=item.get("created_at", ""),
            confidence=item.get("confidence"),
            run_id=item.get("run_id"),
        )

    # ---- nodes ---------------------------------------------------------------------

    def new_id(self, node_type: NodeType) -> str:
        return f"{node_type.value}/{self._ids.uuid7()}"

    def create(self, draft: Node, actor: str) -> Node:
        """Create a non-entity node (entities go through ``upsert_entity``)."""
        if draft.category is Category.ENTITY:
            raise ValidationError("use upsert_entity for entity nodes")
        now = iso(self._clock.now())
        node = NodeFactory.validate(
            replace(
                draft,
                id=draft.id or self.new_id(draft.type),
                created_by=actor,
                created_at=now,
                updated_at=now,
                version=1,
                stamped=True,
                in_degree=0,
                out_degree=0,
            )
        )
        put = {
            "TableName": self._table.name,
            "Item": self._node_item(node),
            "ConditionExpression": "attribute_not_exists(pk)",
        }
        try:
            self._transact(TransactItems=[{"Put": put}, *self._listing_put(node)])
        except ClientError as err:
            if self._condition_failed(err):
                raise ConflictError("node already exists", {"id": node.id}) from err
            raise
        return node

    def create_once(self, draft: Node, actor: str, dedupe_key: str, retry: bool = True) -> tuple[Node, bool]:
        """Create a node, or return the one an earlier call with the same ``dedupe_key`` made.

        The module runtime retries a tool call that timed out with identical arguments, and every
        create used to make a second node: duplicate evidence with no way to tell the copies apart.
        The key item expires after ``DEDUPE_DAYS``, so this is a retry window rather than a
        permanent uniqueness rule.
        """
        key = {"pk": f"D#{dedupe_key}", "sk": "KEY"}
        now = iso(self._clock.now())
        node = NodeFactory.validate(
            replace(
                draft,
                id=draft.id or self.new_id(draft.type),
                created_by=actor,
                created_at=now,
                updated_at=now,
                stamped=True,
            )
        )
        try:
            self._transact(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": self._node_item(node),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": {
                                **key,
                                "kind": "dedupe",
                                "node_id": node.id,
                                "ttl": int(self._clock.now().timestamp()) + DEDUPE_DAYS * 86400,
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    *self._listing_put(node),
                ]
            )
        except ClientError as err:
            if not self._condition_failed(err):
                raise
            raced = self._table.get_item(Key=key, ConsistentRead=True).get("Item")
            if not raced:
                # The node id collided rather than the key, which uuid7 makes vanishingly unlikely:
                # a retryable 409, never a silent second node.
                raise ConflictError("node is being created concurrently; retry") from err
            try:
                return self.get(str(raced["node_id"])), False
            except NotFoundError:
                if not retry:
                    raise
            # The key outlived its node (a person deleted it, or a failed link cleaned it up): it is no
            # longer the record of a create, so it goes and the create is made afresh. Otherwise the
            # same evidence could not be recorded again for a week, and the retry a 409 asked for was a
            # 404 (B3-056, B3-057).
            try:
                self._table.delete_item(
                    Key=key, ConditionExpression="node_id = :n", ExpressionAttributeValues={":n": raced["node_id"]}
                )
            except ClientError as gone:
                if gone.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
            return self.create_once(draft, actor, dedupe_key, retry=False)
        return node, True

    def find_entity(self, node_type: NodeType, name: str) -> Node | None:
        key = NameNormalizer.key(name)
        res = self._table.get_item(Key={"pk": f"K#{node_type.value}#{key}", "sk": "KEY"}, ConsistentRead=True)
        item = res.get("Item")
        if not item:
            return None
        try:
            return self.get(item["node_id"])
        except NotFoundError:  # a name key can outlive its node: no match, not a failed search (r12)
            return None

    def upsert_entity(self, draft: Node, actor: str) -> tuple[Node, bool]:
        """Create the entity, or return the existing one with the same normalised name.

        On a match, empty summary/description are filled and new aliases appended; nothing
        an earlier writer set is overwritten. Returns ``(node, created)``.
        """
        if draft.category is not Category.ENTITY:
            raise ValidationError("upsert_entity only accepts entity types")
        now = iso(self._clock.now())
        node = NodeFactory.validate(
            replace(
                draft,
                id=self.new_id(draft.type),
                created_by=actor,
                created_at=now,
                updated_at=now,
                version=1,
                stamped=True,
                in_degree=0,
                out_degree=0,
            )
        )
        key = f"K#{node.type.value}#{node.name_key}"
        variant = self._variant_entity(node)
        if variant is not None:  # the other spelling is kept as an alias, for keyword search
            return self._merge_into(variant, replace(node, aliases=(*node.aliases, node.title)), actor)
        try:
            self._transact(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": self._node_item(node),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": {"pk": key, "sk": "KEY", "node_id": node.id, "kind": "key"},
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
            return node, True
        except ClientError as err:
            if not self._condition_failed(err):
                raise
        existing = self.find_entity(node.type, node.title)
        if existing is None:
            # The name key outlived its node (a delete that failed part-way): this writer takes it back and creates
            # the entity, so the name is never lost for good (code review r56).
            if self._drop_stale_key(key):
                return self.upsert_entity(draft, actor)
            raise ConflictError("entity is being created concurrently; retry")
        return self._merge_into(existing, node, actor)

    def _drop_stale_key(self, key: str) -> bool:
        """Delete a name key whose node is gone, conditionally on it still naming that node. False when its node
        exists after all (another writer is creating it right now)."""
        item = self._table.get_item(Key={"pk": key, "sk": "KEY"}, ConsistentRead=True).get("Item")
        stale = str(item.get("node_id", "")) if item else ""
        if not stale:
            return True  # gone already: the retry creates it
        try:
            self.get(stale)
            return False
        except NotFoundError:
            pass
        try:
            self._table.delete_item(
                Key={"pk": key, "sk": "KEY"},
                ConditionExpression="node_id = :id",
                ExpressionAttributeValues={":id": stale},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return False  # someone repointed it meanwhile
        return True

    def _variant_entity(self, node: Node) -> Node | None:
        """An existing entity this one is another name for (CTR HC-118, QA-082, HCB-043), found both ways:
        the name with or without a trailing "(alias)", a trailing kind word (SDK, framework, library,
        toolkit) or a leading vendor word (AWS, Amazon), and, for a product or a topic, the same name
        under the other of the two ("Platform Engineering" was forked across both). A company and its
        same-named product are legitimately two nodes, so organizations are left alone. One batch read
        looks every spelling up; the oldest-preferred order is the exact type first, then the spelling
        order of ``_name_variants``."""
        types = [node.type]
        if node.type in _PRODUCT_OR_TOPIC:
            types += [t for t in _PRODUCT_OR_TOPIC if t is not node.type]
        names = _name_variants(node.title, node.type)
        wanted: list[str] = []
        for node_type in types:
            for name in names if node_type is node.type else [node.title, *names]:
                wanted.append(f"K#{node_type.value}#{NameNormalizer.key(name)}")
        wanted = list(dict.fromkeys(wanted))
        found = {item["pk"]: item["node_id"] for item in self._batch_get([{"pk": k, "sk": "KEY"} for k in wanted])}
        # A key can outlive its node (deleted, or a create that failed half way): it is skipped.
        nodes = self.get_many(found.values())
        return next((nodes[found[pk]] for pk in wanted if pk in found and found[pk] in nodes), None)

    def _merge_into(self, existing: Node, node: Node, actor: str) -> tuple[Node, bool]:
        if actor.startswith("module:") and existing.created_by != actor:
            # Another writer's entity (a person's, or another module's): a module may link to it but not
            # rewrite its text, aliases (which steer keyword ranking) or attributes.
            return existing, False
        # Past the alias cap, extra aliases are dropped rather than failing: one noisy writer must not lock
        # the entity against every later upsert.
        merged_aliases = tuple(dict.fromkeys([*existing.aliases, *node.aliases]))[:ALIASES_MAX]
        changes: dict[str, Any] = {}
        if not existing.summary and node.summary:
            changes["summary"] = node.summary
        if not existing.description and node.description:
            changes["description"] = node.description
        if merged_aliases != existing.aliases:
            changes["aliases"] = list(merged_aliases)
        if node.attributes:
            extra = {k: v for k, v in node.attributes.items() if k not in existing.attributes}
            if extra:
                changes["attributes"] = {**existing.attributes, **extra}
        if not changes:
            return existing, False
        if node.private:
            # Filled from private material: the entity is private from now on (B3-003).
            changes["private"] = True
        return self.update(existing.id, changes, existing.version, actor), False

    def _transact(self, TransactItems: list[dict[str, Any]]) -> None:
        """``TransactWriteItems`` that retries transaction conflicts.

        Concurrent writers that touch the same item (most often a popular entity's degree counter,
        when an agent links many pieces of evidence to it in parallel) cancel each other with
        ``TransactionConflict``. Those are retried with jittered backoff; if they persist the caller
        gets a 409 it can retry, never a 500.
        """
        attempt = 0
        while True:
            try:
                self._client.transact_write_items(TransactItems=TransactItems)
                return
            except ClientError as err:
                if not self._conflicted(err):
                    raise
                attempt += 1
                if attempt >= TRANSACTION_ATTEMPTS:
                    raise ConflictError("the graph is busy with concurrent changes; retry shortly") from err
                self._sleep(min(1.0, 0.05 * 2**attempt) * (0.5 + secrets.randbelow(1000) / 1000))

    RETRYABLE_REASONS = frozenset({"TransactionConflict", "ThrottlingError", "ProvisionedThroughputExceeded"})

    @classmethod
    def _conflicted(cls, err: ClientError) -> bool:
        """Whether a cancelled transaction is worth retrying: a conflict with another writer, or
        throttling on a hot item. Throttling used to fall through to the caller as a 500."""
        if err.response["Error"]["Code"] != "TransactionCanceledException":
            return False
        codes = [r.get("Code") for r in err.response.get("CancellationReasons", [])]
        return bool(set(codes) & cls.RETRYABLE_REASONS) and all(
            c in cls.RETRYABLE_REASONS | {"None", None} for c in codes
        )

    @staticmethod
    def _condition_failed(err: ClientError, index: int | None = None) -> bool:
        """True when a transaction was cancelled only because a condition check failed."""
        if err.response["Error"]["Code"] != "TransactionCanceledException":
            return False
        codes = [r.get("Code") for r in err.response.get("CancellationReasons", [])]
        if index is not None:
            return len(codes) > index and codes[index] == "ConditionalCheckFailed"
        return (
            bool(codes)
            and all(c in ("ConditionalCheckFailed", "None", None) for c in codes)
            and "ConditionalCheckFailed" in codes
        )

    def get(self, node_id: str) -> Node:
        return self._node_from_item(self._meta_item(node_id))

    def get_many(self, node_ids: Iterable[str]) -> dict[str, Node]:
        ids = list(dict.fromkeys(node_ids))
        out: dict[str, Node] = {}
        for raw in self._batch_get([{"pk": f"N#{i}", "sk": "META"} for i in ids]):
            node = self._node_from_item(raw)
            out[node.id] = node
        return out

    def _batch_get(self, all_keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Every item of ``all_keys`` that exists, read consistently, 100 keys a call, retrying the keys
        DynamoDB leaves unprocessed."""
        out: list[dict[str, Any]] = []
        resource = self._table.meta.client
        for start in range(0, len(all_keys), 100):
            keys = all_keys[start : start + 100]
            attempts = 0
            while keys:
                if attempts:
                    if attempts >= GET_MANY_ATTEMPTS:
                        # Silently dropping keys made callers report real nodes as missing.
                        raise ServiceUnavailableError("the graph is busy; retry shortly")
                    self._sleep(min(1.0, 0.05 * 2**attempts) * (0.5 + secrets.randbelow(1000) / 1000))
                attempts += 1
                res = resource.batch_get_item(RequestItems={self._table.name: {"Keys": keys, "ConsistentRead": True}})
                out.extend(res.get("Responses", {}).get(self._table.name, []))
                keys = list((res.get("UnprocessedKeys") or {}).get(self._table.name, {}).get("Keys", []))
        return out

    UPDATABLE = frozenset(
        {
            "title",
            "summary",
            "description",
            "attributes",
            "aliases",
            "evidence_url",
            "published_at",
            "confidence",
            "private",
        }
    )

    def update(self, node_id: str, changes: dict[str, Any], expected_version: int, actor: str) -> Node:
        unknown = set(changes) - self.UPDATABLE
        if unknown:
            raise ValidationError(f"fields cannot be updated: {', '.join(sorted(unknown))}")
        current = self.get(node_id)
        if current.version != expected_version:
            raise ConflictError(
                "node was changed by someone else; reload and retry",
                {"expected": expected_version, "actual": current.version},
            )
        if (
            "title" in changes
            and current.category is Category.ENTITY
            and NameNormalizer.key(str(changes["title"])) != current.name_key
        ):
            raise ValidationError("entity titles cannot be renamed to a different name; add an alias instead")
        if "aliases" in changes:
            changes = {**changes, "aliases": tuple(changes["aliases"] or ())}
        # ``private`` only ever rises: a writer that read private material stamps the node private, and
        # nothing lowers it again. Asked for False it is no change at all, so a node written before the
        # stamp existed stays unstamped rather than being declared public (SR2-01, B3-001).
        raise_private = changes.get("private") is True
        changes = {k: v for k, v in changes.items() if k != "private"}
        if raise_private:
            changes = {**changes, "private": True, "stamped": True}
        # A change that only moves whitespace is stored but does not move updated_at, the "changed
        # since" clock the list index is keyed on: a no-op edit cannot refloat an old output (B2-108).
        material = any(
            self._material(getattr(current, k), v) for k, v in changes.items() if k not in ("private", "stamped")
        )
        now = iso(self._clock.now()) if material else current.updated_at
        updated = NodeFactory.validate(replace(current, **changes, updated_at=now, version=current.version + 1))
        del actor  # recorded in the audit log by the caller
        # Write every field except the degree counters: link() and unlink() change those atomically and
        # without bumping the version, so a whole-item put would roll back concurrent edge counts.
        item = {k: v for k, v in self._node_item(updated).items() if k not in _UPDATE_SKIPS}
        cleared = sorted(set(self._node_item(current)) - set(item) - _UPDATE_SKIPS)
        names = {f"#a{i}": k for i, k in enumerate([*item, *cleared])}
        by_key = {k: n for n, k in names.items()}
        values = [f":a{i}" for i in range(len(item))]
        expression = "SET " + ", ".join(f"{by_key[k]} = :a{i}" for i, k in enumerate(item))
        if cleared:
            expression += " REMOVE " + ", ".join(by_key[k] for k in cleared)
        try:
            self._table.update_item(
                Key={"pk": f"N#{node_id}", "sk": "META"},
                UpdateExpression=expression,
                ConditionExpression="version = :v",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues={":v": expected_version, **dict(zip(values, item.values(), strict=True))},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError("node was changed by someone else; reload and retry") from err
            raise
        return updated

    @staticmethod
    def _material(before: Any, after: Any) -> bool:
        """Whether ``after`` differs from ``before`` in more than whitespace."""
        if isinstance(before, str) and isinstance(after, str):
            return before.split() != after.split()
        return bool(before != after)

    def rekey_list_index(
        self, page_size: int = 500, start: dict[str, Any] | None = None, on_page: PageHook | None = None
    ) -> int:
        """Re-key every node's list-index sort key to ``updated_at#id``. Nodes written while the
        index was keyed on created_at keep that key until their next write, so the index would mix
        two clocks; the seed step runs this once per release. Returns how many nodes changed."""
        changed = 0
        kwargs: dict[str, Any] = {
            "Limit": page_size,
            "FilterExpression": "#k = :node",
            "ProjectionExpression": "pk, sk, id, updated_at, gsi1sk",
            "ExpressionAttributeNames": {"#k": "kind"},
            "ExpressionAttributeValues": {":node": "node"},
            **({"ExclusiveStartKey": start} if start else {}),
        }
        while True:
            res = self._table.scan(**kwargs)
            for item in res.get("Items", []):
                want = f"{item.get('updated_at', '')}#{item['id']}"
                if item.get("gsi1sk") == want:
                    continue
                try:
                    self._table.update_item(
                        Key={"pk": item["pk"], "sk": item["sk"]},
                        UpdateExpression="SET gsi1sk = :k",
                        ConditionExpression="updated_at = :u",
                        ExpressionAttributeValues={":k": want, ":u": item.get("updated_at", "")},
                    )
                except ClientError as err:
                    if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                        raise
                    continue  # written meanwhile, which set the new key itself
                changed += 1
            nxt = res.get("LastEvaluatedKey")
            if (on_page is not None and not on_page(nxt)) or not nxt:
                return changed
            kwargs["ExclusiveStartKey"] = nxt

    def rekey_names(
        self, page_size: int = 500, start: dict[str, Any] | None = None, on_page: PageHook | None = None
    ) -> tuple[int, int]:
        """Move every entity name key to the current normalisation. Keys written before names were
        NFKC-folded and stripped of invisible characters no longer match a lookup, so the next
        ``kg_upsert_entity`` of the same name made a duplicate entity (CR2-11). A key whose new form is
        already taken by another entity is left as it is and counted: two entities that the new rule
        says are one name need a person to merge them. Returns (moved, collisions)."""
        moved = collisions = 0
        kwargs: dict[str, Any] = {
            "Limit": page_size,
            "FilterExpression": "#k = :key AND begins_with(pk, :p)",
            "ExpressionAttributeNames": {"#k": "kind"},
            "ExpressionAttributeValues": {":key": "key", ":p": "K#"},
            **({"ExclusiveStartKey": start} if start else {}),
        }
        while True:
            res = self._table.scan(**kwargs)
            for item in res.get("Items", []):
                try:
                    node = self.get(str(item["node_id"]))
                except NotFoundError:
                    continue  # a key left by a crashed delete; the next writer repairs it
                want = f"K#{node.type.value}#{node.name_key}"
                if item["pk"] == want or item["pk"] in self._redirect_keys(node):
                    continue  # its own key, or a merged name that points at it on purpose (code review r58)
                try:
                    self._table.put_item(
                        Item={"pk": want, "sk": "KEY", "node_id": node.id, "kind": "key"},
                        ConditionExpression="attribute_not_exists(pk)",
                    )
                except ClientError as err:
                    if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                        raise
                    collisions += 1
                    continue
                self._table.delete_item(Key={"pk": item["pk"], "sk": "KEY"})
                moved += 1
            nxt = res.get("LastEvaluatedKey")
            if (on_page is not None and not on_page(nxt)) or not nxt:
                return moved, collisions
            kwargs["ExclusiveStartKey"] = nxt

    def delete(self, node_id: str) -> int:
        """Hard-delete a node, its name key and every edge touching it. Returns edges removed."""
        node = self.get(node_id)
        edges = [*self._edges_out(node_id, limit=None, consistent=True), *self._edges_in(node_id, limit=None)]
        with self._table.batch_writer() as batch:
            for e in edges:
                batch.delete_item(Key={"pk": f"N#{e.source}", "sk": f"E#{e.type.value}#{e.target}"})
            if node.category is Category.OUTPUT:
                batch.delete_item(Key=self._listing_key(node))
            batch.delete_item(Key={"pk": f"N#{node_id}", "sk": "META"})
        for e in edges:
            other, field = (e.target, "in_degree") if e.source == node_id else (e.source, "out_degree")
            self._bump(other, field, -1)  # link() refuses self-loops, so ``other`` is never this node
        if node.category is Category.ENTITY:
            # The name key goes only while it is still this entity's, in one conditional delete: after a merge the
            # name resolves to the kept entity, and deleting that key would let the name be created again as a
            # duplicate (code review r54, r55). It goes last, so a failure here leaves only a stale key, which
            # upsert_entity takes back (r56).
            try:
                self._table.delete_item(
                    Key={"pk": f"K#{node.type.value}#{node.name_key}", "sk": "KEY"},
                    ConditionExpression="node_id = :id",
                    ExpressionAttributeValues={":id": node_id},
                )
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        return len(edges)

    def delete_if_unlinked(self, node: Node, *, _retry: bool = True) -> bool:
        """Delete an entity and its name key in one transaction, only if it still has no link in or out.
        ``link`` adds an edge in a transaction that requires both nodes to exist, so a link and this delete
        cannot both succeed: whichever commits first wins (code review r51). Returns whether it was deleted."""
        if node.category is not Category.ENTITY:
            raise ValidationError("delete_if_unlinked only deletes entities")
        unlinked = (
            "(attribute_not_exists(in_degree) OR in_degree = :zero) AND "
            "(attribute_not_exists(out_degree) OR out_degree = :zero)"
        )
        items: list[dict[str, Any]] = [
            {
                "Delete": {
                    "TableName": self._table.name,
                    "Key": {"pk": f"N#{node.id}", "sk": "META"},
                    # Never an entity a live merge holds: its kept entity may have no link yet (code review r66); a
                    # lapsed lock does not stop it (code review r67).
                    "ConditionExpression": f"attribute_exists(pk) AND {unlinked} AND {self._FREE_OR_STALE} "
                    f"AND {self._NOT_MID_MERGE}",
                    "ExpressionAttributeValues": {":zero": 0, ":stale": self._stale()},
                }
            }
        ]
        key = {"pk": f"K#{node.type.value}#{node.name_key}", "sk": "KEY"}
        if self._table.get_item(Key=key, ConsistentRead=True).get("Item", {}).get("node_id") == node.id:
            # The name key goes too, but only while it is still this entity's: after a merge the name resolves
            # to the kept entity, whose key stays (and does not stop this delete; code review r53).
            items.append(
                {
                    "Delete": {
                        "TableName": self._table.name,
                        "Key": key,
                        "ConditionExpression": "node_id = :id",
                        "ExpressionAttributeValues": {":id": node.id},
                    }
                }
            )
        try:
            self._transact(TransactItems=items)
        except ClientError as err:
            if self._condition_failed(err):
                # A waiting duplicate that is gone or finished does not hold it: drop it and try once more (r70).
                return _retry and self._prune_pending(node.id) and self.delete_if_unlinked(node, _retry=False)
            raise
        return True

    # A merge holds both entities while it runs (code review r66): the duplicate carries ``deleting`` (no new link,
    # vote or mark) with ``merge_claim``, the kept entity ``merge_target``, both with ``merge_at``. Neither can be
    # deleted, claimed by another merge, or claimed as a duplicate meanwhile. A lock older than this no longer holds,
    # so a merge that died part way never locks an entity for good.
    _FREE_OR_STALE = "(attribute_not_exists(merge_at) OR merge_at < :stale)"
    # Neither a duplicate whose names a merge moved nor the kept entity it waits on (code review r68, r69, r70).
    _NOT_MID_MERGE = "attribute_not_exists(merge_names_moved) AND attribute_not_exists(merge_pending)"

    def _gone(self, node_ids: Iterable[str]) -> set[str]:
        """The listed duplicates that no longer exist. One that exists still waits on its kept entity: its
        ``merge_names_moved`` is set once and a rerun may only go to the same target (code review r71)."""
        return {d for d in node_ids if self._meta_flags(d) is None}

    def _prune_pending(self, node_id: str) -> bool:
        """Drop from ``merge_pending`` every duplicate that is gone (its delete finished after the merge moved its
        names). Returns whether any was dropped, so the step a stale entry refused can be tried again (code review
        r70, r71)."""
        stale = self._gone((self._meta_flags(node_id) or {}).get("merge_pending") or set())
        if not stale:
            return False
        try:
            self._table.update_item(
                Key={"pk": f"N#{node_id}", "sk": "META"},
                UpdateExpression="DELETE merge_pending :d",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":d": stale},
            )
        except ClientError:
            return False  # nothing dropped: a retry would be refused again (code review r71)
        return True

    def _stale(self) -> str:
        return iso(self._clock.now() - timedelta(seconds=MERGE_LOCK_SECONDS))

    def _merge_held(self, flags: dict[str, Any]) -> bool:
        """Whether a live (not lapsed) merge holds the node, as a duplicate or as the kept entity: the one place
        the lapse rule is read in Python; ``_FREE_OR_STALE`` is the same rule as a condition (code review r68)."""
        held = flags.get("merge_claim") or flags.get("merge_target")
        return bool(held) and str(flags.get("merge_at", "")) >= self._stale()

    def mark_deleting(self, node_id: str, *, _retry: bool = True) -> None:
        """Flag a node as being deleted, before anything about it is cleaned up: from then on ``put_while_exists``
        refuses new votes and working-on marks, so the purge that follows is the last one needed even while the
        delete itself takes seconds (code review r61). Raises ``NotFoundError`` if the node is gone, and
        ``ConflictError`` (to retry) while a merge holds it (code review r66)."""
        try:
            self._table.update_item(
                Key={"pk": f"N#{node_id}", "sk": "META"},
                UpdateExpression="SET deleting = :t REMOVE merge_claim, merge_target, merge_at",
                # Never a duplicate whose names a merge already moved: only that merge, run again, finishes it, so
                # no alias key is left pointing at a deleted entity (code review r68).
                ConditionExpression=f"attribute_exists(pk) AND {self._FREE_OR_STALE} AND {self._NOT_MID_MERGE}",
                ExpressionAttributeValues={":t": True, ":stale": self._stale()},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            flags = self._meta_flags(node_id)
            if flags is None:
                raise NotFoundError(f"node {node_id} not found") from err
            if flags.get("merge_names_moved"):
                raise ConflictError(
                    "a merge of this entity stopped part way; run it again to finish it",
                    {"into": flags["merge_names_moved"]},
                ) from err
            if flags.get("merge_pending") and _retry and self._prune_pending(node_id):
                self.mark_deleting(node_id, _retry=False)
                return
            if flags.get("merge_pending"):
                # The kept entity of a merge that stopped part way: deleting it would strand the duplicate, whose
                # names already point here (code review r69).
                raise ConflictError(
                    "a merge into this entity stopped part way; run it again to finish it",
                    {"from": sorted(flags["merge_pending"])},
                ) from err
            raise ConflictError("this entity is being merged; try again in a moment") from err

    def _meta_flags(self, node_id: str) -> dict[str, Any] | None:
        """The node's delete and merge flags (a consistent read), or None if it is gone."""
        item: dict[str, Any] | None = self._table.get_item(
            Key={"pk": f"N#{node_id}", "sk": "META"},
            ConsistentRead=True,
            ProjectionExpression="pk, deleting, merge_claim, merge_target, merge_at, merge_names_moved, merge_pending",
        ).get("Item")
        return item

    def is_deleting(self, node_id: str) -> bool:
        """Whether the node is flagged by ``mark_deleting`` or a merge claim (a gone node is not)."""
        return bool((self._meta_flags(node_id) or {}).get("deleting"))

    def _claim_merge(self, duplicate_id: str, into_id: str, *, _retry: bool = True) -> str:
        """Lock both entities for a merge in one transaction (code review r65, r66). The duplicate must not be
        flagged (unless by a merge that died) nor be another merge's target; the kept entity must not be flagged nor
        another merge's target. Returns the claim's token. A refused claim is a ``ConflictError``."""
        token = secrets.token_hex(16)
        now, stale = iso(self._clock.now()), self._stale()
        try:
            self._transact(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"N#{duplicate_id}", "sk": "META"},
                            "UpdateExpression": "SET deleting = :t, merge_claim = :tok, merge_at = :now "
                            "REMOVE merge_target",
                            # A duplicate whose names already moved is finished only into the same kept entity
                            # (code review r68).
                            "ConditionExpression": "attribute_exists(pk) AND (attribute_not_exists(deleting) "
                            f"OR attribute_exists(merge_claim)) AND {self._FREE_OR_STALE} AND "
                            "(attribute_not_exists(merge_names_moved) OR merge_names_moved = :into) AND "
                            # Nor the kept entity of a merge that stopped part way (code review r69).
                            "attribute_not_exists(merge_pending)",
                            "ExpressionAttributeValues": {
                                ":t": True,
                                ":tok": token,
                                ":now": now,
                                ":stale": stale,
                                ":into": into_id,
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"N#{into_id}", "sk": "META"},
                            "UpdateExpression": "SET merge_target = :tok, merge_at = :now",
                            "ConditionExpression": "attribute_exists(pk) AND attribute_not_exists(deleting) AND "
                            f"{self._FREE_OR_STALE}",
                            "ExpressionAttributeValues": {":tok": token, ":now": now, ":stale": stale},
                        }
                    },
                ]
            )
        except ClientError as err:
            if self._condition_failed(err):
                if _retry and self._prune_pending(duplicate_id):
                    return self._claim_merge(duplicate_id, into_id, _retry=False)
                # Name what to rerun, as mark_deleting does (code review r72), only when it is the duplicate that
                # refused (code review r73): a refusal by the kept entity has nothing to do with those merges.
                if self._condition_failed(err, 0) and (
                    pending := sorted((self._meta_flags(duplicate_id) or {}).get("merge_pending") or ())
                ):
                    raise ConflictError(
                        "a merge into this entity stopped part way; run it again to finish it", {"from": pending}
                    ) from err
                raise ConflictError("an entity being deleted or merged cannot take part in a merge") from err
            raise
        return token

    def _release_merge(self, duplicate_id: str | None, into_id: str, token: str) -> None:
        """Clear a merge's locks, only while they are still this merge's. The duplicate's is cleared only while no
        run of the merge has moved its names (``merge_names_moved``, kept on the item so a rerun sees it too), so it
        can be run again (code review r65, r66, r67). It runs while another error may be on its way out, so a failure
        here is swallowed: the lock then lapses after MERGE_LOCK_SECONDS."""

        def release(node_id: str, expression: str, condition: str) -> bool:
            try:
                self._table.update_item(
                    Key={"pk": f"N#{node_id}", "sk": "META"},
                    UpdateExpression=expression,
                    ConditionExpression=condition,
                    ExpressionAttributeValues={":tok": token},
                )
            except ClientError:
                return False
            return True

        if duplicate_id is not None and not release(
            duplicate_id,
            "REMOVE deleting, merge_claim, merge_at",
            "merge_claim = :tok AND attribute_not_exists(merge_names_moved)",
        ):
            # Its names moved, so it stays flagged for the rerun; but only the time goes, so the rerun can start at
            # once rather than after the lock lapses (code review r69).
            release(duplicate_id, "REMOVE merge_at", "merge_claim = :tok AND attribute_exists(merge_names_moved)")
        release(into_id, "REMOVE merge_target, merge_at", "merge_target = :tok")

    def _finish_merge(self, duplicate_id: str, into_id: str, token: str) -> None:
        """Release the kept entity of a merge that finished, and forget the duplicate it was waiting for."""
        # Two writes, so a lost lock does not keep the duplicate listed; a failure of either is swallowed: the lock
        # lapses, and a stale entry is pruned by the next step it refuses (code review r70).
        with contextlib.suppress(ClientError):
            self._table.update_item(
                Key={"pk": f"N#{into_id}", "sk": "META"},
                UpdateExpression="DELETE merge_pending :d",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":d": {duplicate_id}},
            )
        self._release_merge(None, into_id, token)

    def _mark_names_moved(self, duplicate_id: str, into_id: str, token: str) -> None:
        """Record on the duplicate, before its name keys move, where they go: from then on no run of the merge
        releases it, nothing deletes it, and only a merge into the same kept entity finishes it (code review r67,
        r68). A lost claim is a ``ConflictError``."""
        # The kept entity records the duplicate in the same transaction: it cannot be deleted or merged away while
        # the duplicate waits on it (code review r69).
        try:
            self._transact(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"N#{duplicate_id}", "sk": "META"},
                            "UpdateExpression": "SET merge_names_moved = :into",
                            "ConditionExpression": "merge_claim = :tok",
                            "ExpressionAttributeValues": {":into": into_id, ":tok": token},
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"N#{into_id}", "sk": "META"},
                            "UpdateExpression": "ADD merge_pending :d",
                            "ConditionExpression": "merge_target = :tok",
                            "ExpressionAttributeValues": {":d": {duplicate_id}, ":tok": token},
                        }
                    },
                ]
            )
        except ClientError as err:
            if self._condition_failed(err):
                raise ConflictError("the merge lost its lock; try again") from err
            raise

    def put_while_exists(self, node_id: str, table_name: str, item: dict[str, Any]) -> None:
        """Write ``item`` to ``table_name`` only while node ``node_id`` exists and is not being deleted
        (``mark_deleting``), in one transaction: a vote or a working-on mark (which hold an email) can then never
        be written after the purge that runs before the delete (code review r59, r60, r61). Raises
        ``NotFoundError`` if the node is gone or going. A busy node can make it a 409 to retry, as for a link."""
        try:
            self._transact(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"N#{node_id}", "sk": "META"},
                            "ConditionExpression": "attribute_exists(pk) AND attribute_not_exists(deleting)",
                        }
                    },
                    {"Put": {"TableName": table_name, "Item": item}},
                ]
            )
        except ClientError as err:
            if self._condition_failed(err):
                raise NotFoundError(f"node {node_id} not found") from err
            raise

    def merge_entity(
        self, duplicate_id: str, into_id: str, actor: str, *, apply: bool = False, private: bool = False
    ) -> dict[str, Any]:
        """Fold a duplicate entity into the one it is another name for (CTR HC-105/120/124/140). A dry run
        (the default) says what would happen; ``apply`` does it: every edge moves to the kept entity
        (with its writer, confidence and run), links in other nodes' descriptions are pointed at it, it
        gains the duplicate's name and aliases and any text it lacked, the duplicate is deleted, and the
        duplicate's name key resolves to the kept entity so the next write of that name lands there.
        ``private`` says the duplicate is hidden from viewers (the caller's hider): the kept entity is then
        stamped private before anything moves onto it (security review r66)."""
        if duplicate_id == into_id:
            raise ValidationError("an entity cannot be merged into itself")
        dup_item, into_item = self._meta_item(duplicate_id), self._meta_item(into_id)
        dup, into = self._node_from_item(dup_item), self._node_from_item(into_item)
        if dup.category is not Category.ENTITY or into.category is not Category.ENTITY:
            raise ValidationError("only entities can be merged")
        if dup.type is not into.type and not {dup.type, into.type} <= set(_PRODUCT_OR_TOPIC):
            raise ValidationError(f"a {dup.type.value} cannot be merged into a {into.type.value}")
        moved_to = dup_item.get("merge_names_moved")
        if moved_to and moved_to != into_id:
            raise ConflictError(
                "a merge of this entity into another stopped part way; finish that one", {"into": moved_to}
            )
        if not apply:
            # Read only: a waiting duplicate that is gone does not count, and is pruned by the apply (code review r71).
            pending = set(dup_item.get("merge_pending") or ())
            pending -= self._gone(pending)
            # Every move into a dying entity would be refused (security and code review r64). A duplicate left by a
            # merge that died is not dying: the dry run plans the rerun that finishes it (code review r68).
            if pending:
                raise ConflictError(
                    "a merge into this entity stopped part way; run it again to finish it", {"from": sorted(pending)}
                )
            if (
                self._merge_held(dup_item)
                or (dup_item.get("deleting") and not dup_item.get("merge_claim"))
                or into_item.get("deleting")
                or self._merge_held(into_item)
            ):
                raise ConflictError("an entity being deleted or merged cannot take part in a merge")
            return self._merge_plan(dup, into)[0]
        # Both entities are locked from here on (code review r64, r65, r66): the duplicate takes no new link, and
        # neither can be deleted or merged elsewhere. A merge that stops before the names move releases both, so it
        # can be run again; after that it keeps the duplicate's lock, which lapses, so a rerun finishes it.
        token = self._claim_merge(duplicate_id, into_id)
        try:
            # Read again under the lock: the snapshot above may predate a write the claim has since shut out.
            report = self._apply_merge(
                self.get(duplicate_id),
                self.get(into_id),
                actor,
                private,
                lambda: self._mark_names_moved(duplicate_id, into_id, token),
            )
        except Exception:
            self._release_merge(duplicate_id, into_id, token)
            raise
        self._finish_merge(duplicate_id, into_id, token)
        return report

    def _meta_item(self, node_id: str) -> dict[str, Any]:
        node_type_of(node_id)
        item: dict[str, Any] | None = self._table.get_item(
            Key={"pk": f"N#{node_id}", "sk": "META"}, ConsistentRead=True
        ).get("Item")
        if not item:
            raise NotFoundError(f"node {node_id} not found", {"id": node_id})
        return item

    def _merge_plan(
        self, dup: Node, into: Node, *, consistent: bool = False
    ) -> tuple[dict[str, Any], list[tuple[Edge, str, str]], dict[str, Node]]:
        duplicate_id, into_id = dup.id, into.id
        moves: list[tuple[Edge, str, str]] = []
        dropped: list[dict[str, str]] = []
        for e in [
            *self._edges_out(duplicate_id, limit=None, consistent=consistent),
            *self._edges_in(duplicate_id, limit=None),
        ]:
            src = into_id if e.source == duplicate_id else e.source
            dst = into_id if e.target == duplicate_id else e.target
            try:
                if src == dst:
                    raise ValidationError("it would link the kept entity to itself")
                Registry.check_edge(e.type, node_type_of(src), node_type_of(dst))
            except ValidationError as why:
                dropped.append({"from": e.source, "type": e.type.value, "to": e.target, "why": why.message})
                continue
            moves.append((e, src, dst))
        link = f"/insights/nodes/{duplicate_id}"
        others = self.get_many({o for e, src, dst in moves for o in (src, dst) if o != into_id})
        rewrite = sorted(n.id for n in others.values() if link in n.description)
        aliases = [a for a in dict.fromkeys([dup.title, *dup.aliases]) if a not in into.aliases and a != into.title]
        report: dict[str, Any] = {
            "duplicate": duplicate_id,
            "into": into_id,
            "applied": False,
            "edges": len(moves) + len(dropped),
            "moved": len(moves),
            "dropped": dropped,
            "redirected": [],
            "aliases_added": aliases,
            "descriptions_rewritten": rewrite,
        }
        return report, moves, others

    def _apply_merge(
        self, dup: Node, into: Node, actor: str, private: bool, names_moved: Callable[[], None]
    ) -> dict[str, Any]:
        duplicate_id, into_id = dup.id, into.id
        report, moves, others = self._merge_plan(dup, into, consistent=True)
        report["applied"] = True
        if (private or dup.private) and not into.private:
            # Stamped before any edge, name or text moves onto it, so nothing hidden is ever shown (security review
            # r66).
            into = self.update(into_id, {"private": True}, into.version, actor)
        link = f"/insights/nodes/{duplicate_id}"
        aliases = report["aliases_added"]
        for e, src, dst in moves:
            try:
                self.link(src, e.type, dst, e.created_by or actor, confidence=e.confidence, run_id=e.run_id)
            except NotFoundError:
                # The kept entity is locked, so the other end is the one refused. Being deleted, the edge goes with
                # it (code review r63); held by another merge, it is not lost: stop, to be run again (code review r66).
                other = src if dst == into_id else dst
                # A live merge, or a duplicate a merge already moved the names of and that waits for its rerun, is not
                # being deleted (code review r70); a lapsed merge that moved nothing is (code review r67, r68).
                flags = self._meta_flags(other) or {}
                if self._merge_held(flags):
                    raise ConflictError("a linked entity is being merged; try again in a moment") from None
                why = "being deleted"
                if moved_to := flags.get("merge_names_moved"):
                    # Its own merge would take this edge to its kept entity: take it there now (code review r71), or
                    # drop it where that merge would drop it (code review r72).
                    try:
                        linked, reason = self._relink_to(e, src, dst, other, str(moved_to), actor)
                    except NotFoundError:
                        raise ConflictError(
                            "a linked entity's merge stopped part way; finish it first",
                            {"entity": other, "into": moved_to},
                        ) from None
                    if linked:
                        # Counted apart from the moves, which keep their planned ends (code review r73): edges ==
                        # moved + dropped + redirected (code review r74).
                        report["moved"] -= 1
                        report["redirected"].append(
                            {"from": e.source, "type": e.type.value, "to": e.target, "via": moved_to}
                        )
                        continue
                    if reason is None:
                        continue  # already there: counted as moved, like a plain move onto an existing edge
                    why = reason
                report["moved"] -= 1
                report["dropped"].append({"from": e.source, "type": e.type.value, "to": e.target, "why": why})
        for node_id in report["descriptions_rewritten"]:
            node = others[node_id]
            self.update(
                node_id,
                {"description": node.description.replace(link, f"/insights/nodes/{into_id}")},
                node.version,
                actor,
            )
        self._merge_into(self.get(into_id), replace(dup, aliases=tuple(aliases)), actor)
        # Recorded after the aliases are copied (a conflict there still releases the duplicate, to be run again at
        # once) and before any name key moves (code review r68).
        names_moved()
        # Every name that resolves to the duplicate (its own, and those of entities merged into it before, which are
        # its aliases) points at the kept entity before the duplicate goes: no moment when a name is free, and no
        # redirect left pointing at a deleted entity (code review r56, r57). Only keys that name the duplicate move.
        # A product and a topic can be merged into each other, so a name the duplicate took over from an earlier merge
        # can sit under either type's key: each is looked at (security review r58). The keys are read in one batch
        # and only those that name the duplicate are written, plus the duplicate's own key if it is missing; a stored
        # name with no key (one kept from before aliases needed one) is skipped, never fatal (code review r58).
        own_pk = f"K#{dup.type.value}#{dup.name_key}"
        wanted = list(dict.fromkeys([own_pk, *sorted(self._redirect_keys(dup))]))
        found = {item["pk"]: item["node_id"] for item in self._batch_get([{"pk": k, "sk": "KEY"} for k in wanted])}
        for pk in wanted:
            own = pk == own_pk
            current = found.get(pk)
            if current != duplicate_id and not (own and current is None):
                continue  # a third entity's key, or (for an alias) no key at all
            try:
                self._table.put_item(
                    Item={"pk": pk, "sk": "KEY", "node_id": into_id, "kind": "key"},
                    ConditionExpression="attribute_not_exists(pk) OR node_id = :dup" if own else "node_id = :dup",
                    ExpressionAttributeValues={":dup": duplicate_id},
                )
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        self.delete(duplicate_id)  # locked by the claim, so no delete of it can have started meanwhile (r66)
        return report

    def _relink_to(self, e: Edge, src: str, dst: str, other: str, moved_to: str, actor: str) -> tuple[bool, str | None]:
        """Link ``src``-``dst`` with ``other`` replaced by the entity its names moved to. Returns ``(linked, why)``:
        ``(True, None)`` when it made the edge, ``(False, None)`` when the edge was there already, and
        ``(False, why)`` when it is dropped, as that entity's own merge would drop it (a self-link, or an edge type
        that does not fit; code review r72, r74). ``NotFoundError`` when that entity refuses the link too."""
        src, dst = (moved_to, dst) if src == other else (src, moved_to)
        # Only the retarget rule drops the edge; any other refusal from link() is raised as in a plain move (r73).
        try:
            if src == dst:
                raise ValidationError("it would link the kept entity to itself")
            Registry.check_edge(e.type, node_type_of(src), node_type_of(dst))
        except ValidationError as why:
            return False, why.message
        return self.link(src, e.type, dst, e.created_by or actor, confidence=e.confidence, run_id=e.run_id)[1], None

    @staticmethod
    def _redirect_keys(node: Node) -> set[str]:
        """The name keys a merge may have pointed at ``node``: one per alias, under each type the alias can have
        come from (a product and a topic merge into each other), and its own title under the other of the two (a
        topic merged into a product of the same name adds no alias, code review r59). An alias with no key has none."""
        others = [t for t in _PRODUCT_OR_TOPIC if node.type in _PRODUCT_OR_TOPIC and t is not node.type]
        keys = {k for a in node.aliases if (k := NameNormalizer.key_or_none(a))}
        found = {f"K#{t.value}#{k}" for k in keys for t in [node.type, *others]}
        return found | {f"K#{t.value}#{node.name_key}" for t in others}

    def entity_hygiene(self, cap: int = 5000, show: int = 100) -> dict[str, Any]:
        """What an administrator tidies (CTR HC-120/124/140): entities with no links at all, and groups of
        entities that look like one thing (the same name under another spelling, an alias that is
        another entity's name), each group with the best-linked entity first, as the one to keep."""
        orphans: list[Node] = []
        by_key: dict[str, list[Node]] = {}
        scanned = 0
        for node_type in sorted(ENTITIES, key=lambda t: t.value):
            for node in self.iterate(node_type, cap=cap):
                scanned += 1
                if node.degree == 0:
                    orphans.append(node)
                names = [node.title, *node.aliases, *_name_variants(node.title, node.type)]
                group = "pt" if node.type in _PRODUCT_OR_TOPIC else node.type.value
                for key in {k for n in names if (k := NameNormalizer.key_or_none(n))}:  # code review r58, r59
                    by_key.setdefault(f"{group}#{key}", []).append(node)
        groups: dict[tuple[str, ...], list[Node]] = {}
        for nodes in by_key.values():
            unique = {n.id: n for n in nodes}
            if len(unique) > 1:
                ordered = sorted(unique.values(), key=lambda n: (-n.degree, n.created_at, n.id))
                groups[tuple(n.id for n in ordered)] = ordered

        def brief(n: Node) -> dict[str, Any]:
            return {"id": n.id, "type": n.type.value, "title": n.title, "degree": n.degree}

        likely = sorted(groups.values(), key=lambda g: (-sum(n.degree for n in g), g[0].id))
        return {
            "scanned": scanned,
            "orphans": [brief(n) for n in sorted(orphans, key=lambda n: n.created_at)[:show]],
            "orphan_count": len(orphans),
            "duplicates": [[brief(n) for n in g] for g in likely[:show]],
            "duplicate_count": len(likely),
        }

    def recent(
        self,
        node_type: NodeType,
        *,
        insight_kind: str | None = None,
        after: str | None = None,
        limit: int = 25,
        cursor: str | None = None,
        oldest_first: bool = False,
    ) -> Page:
        if node_type is NodeType.INSIGHT and not insight_kind:
            raise ValidationError("insight listing needs an insight_kind")
        partition = f"T#insight#{insight_kind}" if node_type is NodeType.INSIGHT else f"T#{node_type.value}"
        kwargs: dict[str, Any] = {
            "IndexName": "gsi1",
            "Limit": max(1, min(limit, MAX_PAGE)),
            "ScanIndexForward": oldest_first,
            "KeyConditionExpression": "gsi1pk = :p" + (" AND gsi1sk > :a" if after else ""),
            # ``after`` is a timestamp (strictly after that millisecond) or an item's ``<updated_at>#<id>``
            # (strictly after that item).
            "ExpressionAttributeValues": {
                ":p": partition,
                **({":a": after if "#" in after else f"{after}#~"} if after else {}),
            },
        }
        start = decode_cursor(cursor, INDEX_KEYS, ("gsi1pk", partition))
        if start:
            kwargs["ExclusiveStartKey"] = start
        res = self._table.query(**kwargs)
        return Page([self._node_from_item(i) for i in res.get("Items", [])], encode_cursor(res.get("LastEvaluatedKey")))

    def newest(
        self, node_type: NodeType, *, insight_kind: str | None = None, limit: int = 25, cursor: str | None = None
    ) -> Page:
        """Outputs newest-created first, for people's lists (``recent`` orders by updated_at, for the
        modules' cursors)."""
        if node_type is NodeType.INSIGHT and not insight_kind:
            raise ValidationError("insight listing needs an insight_kind")
        partition = f"L#insight#{insight_kind}" if node_type is NodeType.INSIGHT else f"L#{node_type.value}"
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :p",
            "ExpressionAttributeValues": {":p": partition},
            "ScanIndexForward": False,
            "Limit": max(1, min(limit, MAX_PAGE)),
        }
        start = decode_cursor(cursor, TABLE_KEYS, ("pk", partition))
        if start:
            kwargs["ExclusiveStartKey"] = start
        res = self._table.query(**kwargs)
        ids = [str(i["node_id"]) for i in res.get("Items", [])]
        found = self.get_many(ids)
        return Page([found[i] for i in ids if i in found], encode_cursor(res.get("LastEvaluatedKey")))

    def index_created(
        self, page_size: int = 500, start: dict[str, Any] | None = None, on_page: PageHook | None = None
    ) -> int:
        """One-time migration: give every output written before the created_at listing existed its place
        in it. Idempotent. Returns how many listings were written."""
        written = 0
        kwargs: dict[str, Any] = {
            "Limit": page_size,
            "FilterExpression": "#k = :node",
            "ProjectionExpression": "pk, sk, id, #t, insight_kind, created_at",
            "ExpressionAttributeNames": {"#k": "kind", "#t": "type"},
            "ExpressionAttributeValues": {":node": "node"},
            **({"ExclusiveStartKey": start} if start else {}),
        }
        while True:
            res = self._table.scan(**kwargs)
            for item in res.get("Items", []):
                node_type = NodeType(str(item["type"]))
                if node_type.category is not Category.OUTPUT:
                    continue
                stub = Node(
                    id=str(item["id"]),
                    type=node_type,
                    title="",
                    insight_kind=item.get("insight_kind"),
                    created_at=str(item.get("created_at", "")),
                )
                self._table.put_item(Item={**self._listing_key(stub), "kind": "listing", "node_id": stub.id})
                written += 1
            nxt = res.get("LastEvaluatedKey")
            if (on_page is not None and not on_page(nxt)) or not nxt:
                return written
            kwargs["ExclusiveStartKey"] = nxt

    def iterate(self, node_type: NodeType, *, insight_kind: str | None = None, cap: int = 5000) -> Iterator[Node]:
        cursor: str | None = None
        seen = 0
        while True:
            page = self.recent(node_type, insight_kind=insight_kind, limit=MAX_PAGE, cursor=cursor)
            for node in page.items:
                seen += 1
                yield node
                if seen >= cap:
                    return
            if not page.cursor:
                return
            cursor = page.cursor

    # ---- edges ---------------------------------------------------------------------

    def link(
        self,
        source_id: str,
        edge: EdgeType,
        target_id: str,
        actor: str,
        *,
        confidence: float | None = None,
        run_id: str | None = None,
    ) -> tuple[Edge, bool]:
        if source_id == target_id:
            raise ValidationError("a node cannot link to itself")
        src_type, dst_type = node_type_of(source_id), node_type_of(target_id)
        Registry.check_edge(edge, src_type, dst_type)
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValidationError("confidence must be between 0 and 1")
        now = iso(self._clock.now())
        item: dict[str, Any] = {
            "pk": f"N#{source_id}",
            "sk": f"E#{edge.value}#{target_id}",
            "gsi1pk": f"R#{target_id}",
            "gsi1sk": f"{edge.value}#{source_id}",
            "kind": "edge",
            "source": source_id,
            "target": target_id,
            "edge_type": edge.value,
            "created_by": actor,
            "created_at": now,
        }
        if confidence is not None:
            item["confidence"] = confidence
        if run_id:
            item["run_id"] = run_id
        name = self._table.name
        try:
            self._transact(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": name,
                            "Item": _to_ddb(item),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Update": {
                            "TableName": name,
                            "Key": {"pk": f"N#{source_id}", "sk": "META"},
                            "UpdateExpression": "ADD out_degree :one",
                            # Never onto a node being deleted: its edges were already read (code review r62).
                            "ConditionExpression": "attribute_exists(pk) AND attribute_not_exists(deleting)",
                            "ExpressionAttributeValues": {":one": 1},
                        }
                    },
                    {
                        "Update": {
                            "TableName": name,
                            "Key": {"pk": f"N#{target_id}", "sk": "META"},
                            "UpdateExpression": "ADD in_degree :one",
                            "ConditionExpression": "attribute_exists(pk) AND attribute_not_exists(deleting)",
                            "ExpressionAttributeValues": {":one": 1},
                        }
                    },
                ]
            )
        except ClientError as err:
            if not self._condition_failed(err):
                raise
            if self._condition_failed(err, 0):
                return self._edge_from_item(self._get_edge(source_id, edge, target_id)), False
            raise NotFoundError(
                "both nodes must exist before they can be linked", {"from": source_id, "to": target_id}
            ) from err
        return self._edge_from_item(_to_ddb(item)), True

    def unlink(self, source_id: str, edge: EdgeType, target_id: str) -> bool:
        res = self._table.delete_item(
            Key={"pk": f"N#{source_id}", "sk": f"E#{edge.value}#{target_id}"}, ReturnValues="ALL_OLD"
        )
        if not res.get("Attributes"):
            return False
        self._bump(source_id, "out_degree", -1)
        self._bump(target_id, "in_degree", -1)
        return True

    def _get_edge(self, source_id: str, edge: EdgeType, target_id: str) -> dict[str, Any]:
        res = self._table.get_item(
            Key={"pk": f"N#{source_id}", "sk": f"E#{edge.value}#{target_id}"}, ConsistentRead=True
        )
        return dict(res["Item"])

    def _bump(self, node_id: str, field: str, delta: int) -> None:
        try:
            self._table.update_item(
                Key={"pk": f"N#{node_id}", "sk": "META"},
                UpdateExpression=f"ADD {field} :d",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":d": delta},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def _query_all(self, kwargs: dict[str, Any], limit: int | None) -> list[Edge]:
        """Edges from a query, following pages until ``limit`` (``None``: every page)."""
        found: list[Edge] = []
        while True:
            page_kwargs = {**kwargs, **({"Limit": limit - len(found)} if limit is not None else {})}
            res = self._table.query(**page_kwargs)
            found += [self._edge_from_item(i) for i in res.get("Items", [])]
            start = res.get("LastEvaluatedKey")
            if not start or (limit is not None and len(found) >= limit):
                return found
            kwargs = {**kwargs, "ExclusiveStartKey": start}

    def _edges_out(
        self, node_id: str, edge: EdgeType | None = None, limit: int | None = 1000, *, consistent: bool = False
    ) -> list[Edge]:
        """``consistent``: a strongly consistent read, for a delete that must see a link committed a moment ago
        (code review r63). Out-edges live in the node's own partition, so they can be read that way."""
        prefix = f"E#{edge.value}#" if edge else "E#"
        return self._query_all(
            {
                "KeyConditionExpression": "pk = :p AND begins_with(sk, :s)",
                "ExpressionAttributeValues": {":p": f"N#{node_id}", ":s": prefix},
                **({"ConsistentRead": True} if consistent else {}),
            },
            limit,
        )

    def _edges_in(self, node_id: str, edge: EdgeType | None = None, limit: int | None = 1000) -> list[Edge]:
        kwargs: dict[str, Any] = {
            "IndexName": "gsi1",
            "KeyConditionExpression": "gsi1pk = :p",
            "ExpressionAttributeValues": {":p": f"R#{node_id}"},
        }
        if edge:
            kwargs["KeyConditionExpression"] += " AND begins_with(gsi1sk, :s)"
            kwargs["ExpressionAttributeValues"][":s"] = f"{edge.value}#"
        return self._query_all(kwargs, limit)

    def newest_links(
        self,
        node_id: str,
        *,
        direction: str,
        edge_types: Iterable[EdgeType],
        node_types: Iterable[NodeType],
        per_type: int,
    ) -> list[Edge]:
        """The newest ``per_type`` links of each (edge type, node type) pair. Edge keys sort by the other
        end's type and then its id, and ids are time-ordered within a type (uuid7), so reading each pair's
        key range backwards gives its newest links; one scan over the whole range would instead stop at
        the alphabetically first types (code review r8)."""
        if direction not in ("in", "out"):
            raise ValidationError("direction must be in or out")
        found: list[Edge] = []
        for edge in edge_types:
            for node_type in node_types:
                if direction == "in":
                    kwargs: dict[str, Any] = {
                        "IndexName": "gsi1",
                        "KeyConditionExpression": "gsi1pk = :p AND begins_with(gsi1sk, :s)",
                        "ExpressionAttributeValues": {":p": f"R#{node_id}", ":s": f"{edge.value}#{node_type.value}/"},
                    }
                else:
                    kwargs = {
                        "KeyConditionExpression": "pk = :p AND begins_with(sk, :s)",
                        "ExpressionAttributeValues": {":p": f"N#{node_id}", ":s": f"E#{edge.value}#{node_type.value}/"},
                    }
                found += self._query_all({**kwargs, "ScanIndexForward": False}, per_type)
        return found

    def edges(
        self,
        node_id: str,
        *,
        direction: str = "both",
        edge: EdgeType | None = None,
        limit: int | None = MAX_EDGES_PER_HOP,
        consistent: bool = False,
    ) -> list[Edge]:
        """A node's edges, up to ``limit`` (``None``: all of them). ``consistent`` reads the out-edges strongly
        consistently (in-edges come from an index, which cannot be)."""
        if direction not in ("in", "out", "both"):
            raise ValidationError("direction must be in, out or both")
        found: list[Edge] = []
        if direction in ("out", "both"):
            found.extend(self._edges_out(node_id, edge, limit, consistent=consistent))
        if direction in ("in", "both"):
            found.extend(self._edges_in(node_id, edge, limit))
        return found if limit is None else found[:limit]

    def prune(self, node_id: str, limit: int, keep: EdgeType | None = None, kept: int = 0) -> int:
        """Remove up to ``limit`` of a node's edges (and their degree counts), so a node with more links
        than one request can delete is taken apart over several sweeps (B3-069). Edges of type ``keep`` are left
        in place (an idea's drafts must not lose their link, code review r59); ``kept`` says how many of them the
        node has at most, so the read stays bounded at ``limit + kept`` edges (code review r60). Returns how many
        went."""
        removed = 0
        found = self.edges(node_id, limit=limit + (kept if keep is not None else 0))
        for e in [e for e in found if e.type is not keep][:limit]:
            removed += int(self.unlink(e.source, e.type, e.target))
        return removed

    def neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 1,
        edge_types: Iterable[EdgeType] | None = None,
        node_types: Iterable[NodeType] | None = None,
        max_nodes: int = 150,
        blocked: Callable[[Node], bool] | None = None,
        offset: int | None = None,
    ) -> Neighborhood:
        """``blocked`` nodes are dropped before the next hop, so a caller that may not read a node
        cannot reach anything only that node connects to (and never learns it is there). ``offset`` pages
        through a node's direct neighbours (depth 1 only), in a stable order, ``max_nodes - 1`` at a time."""
        if not 1 <= depth <= MAX_BFS_DEPTH:
            raise ValidationError(f"depth must be between 1 and {MAX_BFS_DEPTH}")
        allowed_edges = set(edge_types) if edge_types else None
        allowed_nodes = set(node_types) if node_types else None
        root = self.get(node_id)
        if offset is not None:
            if depth != 1 or offset < 0:
                raise ValidationError("offset pages direct neighbours only: depth 1 and an offset of 0 or more")
            return self._neighbour_page(root, offset, max_nodes, allowed_edges, allowed_nodes, blocked)
        frontier, seen_nodes = [node_id], {node_id}
        edges: dict[tuple[str, str, str], Edge] = {}
        truncated = False
        for _ in range(depth):
            nxt: list[str] = []
            for current in frontier:
                for e in self.edges(current):
                    if allowed_edges is not None and e.type not in allowed_edges:
                        continue
                    other = e.target if e.source == current else e.source
                    if allowed_nodes is not None and node_type_of(other) not in allowed_nodes:
                        continue
                    edges[(e.source, e.type.value, e.target)] = e
                    if other not in seen_nodes:
                        if len(seen_nodes) >= max_nodes:
                            truncated = True
                            continue
                        seen_nodes.add(other)
                        nxt.append(other)
            if blocked is not None and nxt:
                reachable = self.get_many(nxt)
                hidden = {i for i in nxt if i not in reachable or blocked(reachable[i])}
                seen_nodes -= hidden
                nxt = [i for i in nxt if i not in hidden]
            frontier = nxt
            if not frontier:
                break
        nodes = self.get_many(seen_nodes)
        nodes[root.id] = root
        kept = [e for e in edges.values() if e.source in nodes and e.target in nodes]
        return Neighborhood([nodes[i] for i in seen_nodes if i in nodes], kept, truncated)

    def _neighbour_page(
        self,
        root: Node,
        offset: int,
        max_nodes: int,
        allowed_edges: set[EdgeType] | None,
        allowed_nodes: set[NodeType] | None,
        blocked: Callable[[Node], bool] | None,
    ) -> Neighborhood:
        by_other: dict[str, list[Edge]] = {}
        for e in self.edges(root.id):
            if allowed_edges is not None and e.type not in allowed_edges:
                continue
            other = e.target if e.source == root.id else e.source
            if other == root.id or (allowed_nodes is not None and node_type_of(other) not in allowed_nodes):
                continue
            by_other.setdefault(other, []).append(e)
        ordered = sorted(by_other)
        if blocked is not None:
            # Filtered before paging, so the total and the pages count only what the caller may see:
            # a hidden neighbour is never counted (security review r7).
            found = self.get_many(ordered)
            ordered = [i for i in ordered if i in found and not blocked(found[i])]
            page = ordered[offset : offset + max(max_nodes - 1, 1)]
            nodes = {i: found[i] for i in page}
        else:
            page = ordered[offset : offset + max(max_nodes - 1, 1)]
            nodes = self.get_many(page)
        kept = [e for i in page if i in nodes for e in by_other[i]]
        truncated = offset + len(page) < len(ordered)
        return Neighborhood([root, *(nodes[i] for i in page if i in nodes)], kept, truncated, len(ordered), offset)


_PRODUCT_OR_TOPIC = (NodeType.PRODUCT, NodeType.TOPIC)
_TRAILING_ALIAS = re.compile(r"\s*\([^()]{1,60}\)\s*$")
# The words a name can gain or lose and still be the same entity (CTR HC-118, QA-082).
ENTITY_KIND_WORDS = ("SDK", "framework", "library", "toolkit")
ENTITY_VENDOR_WORDS = ("AWS", "Amazon")
# A company's legal form: "Acme Corp", "Acme Corporation" and "Acme Inc." are one company (campaign r2).
ENTITY_LEGAL_WORDS = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "ltd",
        "limited",
        "llc",
        "plc",
        "gmbh",
        "ag",
        "pte",
        "pty",
        "bhd",
        "tbk",
    }
)
_KIND_LOWER = {w.lower() for w in ENTITY_KIND_WORDS}
_VENDOR_LOWER = {w.lower() for w in ENTITY_VENDOR_WORDS}


def _name_variants(name: str, node_type: NodeType | None = None) -> list[str]:
    """Other spellings of the same entity name, not the name itself, both ways: "Model Context Protocol
    (MCP)" -> "Model Context Protocol"; "Strands Agents SDK" <-> "Strands Agents" <-> "AWS Strands
    Agents". A word is dropped or added only when two or more words remain, so "Lambda" never pairs
    with "Lambda SDK" or "AWS Lambda". Words, not letters: "AWSome Awards" is left alone."""
    stripped = name.strip()
    base = _TRAILING_ALIAS.sub("", stripped).strip() or stripped
    words = base.split()
    if len(words) >= 3 and words[0].lower() in _VENDOR_LOWER:
        words = words[1:]
    if len(words) >= 3 and words[-1].lower() in _KIND_LOWER:
        words = words[:-1]
    # A company's legal form, only as the last word of an organization's name and only when a word is
    # left: "Acme Corp" -> "Acme", never "Co Op Bank" -> "Op Bank" or a topic's "Limited Liability".
    legal = (
        words[:-1]
        if node_type is NodeType.ORGANIZATION
        and len(words) >= 2
        and words[-1].lower().rstrip(".,") in ENTITY_LEGAL_WORDS
        else words
    )
    variants = [base, " ".join(words), *([" ".join(legal)] if legal != words else [])]
    if len(words) >= 2:
        for vendor in ENTITY_VENDOR_WORDS:
            variants.append(" ".join([vendor, *words]))
        for kind in ENTITY_KIND_WORDS:
            variants.append(" ".join([*words, kind]))
            variants.extend(" ".join([vendor, *words, kind]) for vendor in ENTITY_VENDOR_WORDS)
    own = NameNormalizer.key(stripped)
    out: dict[str, str] = {}
    for v in variants:
        key = NameNormalizer.key(v)
        if key and key != own and key not in out:
            out[key] = v
    return list(out.values())
