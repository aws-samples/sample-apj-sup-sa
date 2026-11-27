# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Insight browsing: list pages per output kind, detail with evidence, feedback, working-on.

"Kinds" in URLs: ``signals``, ``trends``, ``gaps``, ``content``, ``drafts`` and
``insight:<insight_kind>`` for custom modules. Detail views split evidence into supporting and
contradicting so the minority counter-view stays visible (the internal radar's sec. 46.5).

Personal data: feedback records store the voter's email address and Cognito subject with each
vote and comment, and the admin feedback list returns them. They stay in the deployment's
DynamoDB table in the customer's account. The deploying customer is responsible for meeting
the data-protection rules that apply to them (for example GDPR), including notices to users and
deleting a user's records on request; see the AWS shared responsibility model and
https://aws.amazon.com/compliance/.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from boto3.dynamodb.conditions import Key

from insights_radar.core.clock import Clock, SystemClock, iso
from insights_radar.core.dynamo import PageHook, Query
from insights_radar.core.errors import NotFoundError, ValidationError
from insights_radar.core.names import NameNormalizer
from insights_radar.graph.model import INSIGHT_KIND_RE, Node
from insights_radar.graph.registry import Category, EdgeType, NodeType
from insights_radar.graph.repository import INDEX_KEYS, GraphRepository, decode_cursor, encode_cursor
from insights_radar.graph.search import HybridSearch
from insights_radar.identity.principal import Principal
from insights_radar.modules.attributes import AttributeField, AttributeSchema, FieldType

PAGE_SIZE = 25
QUERY_CANDIDATES = 50
DETAIL_EDGES_PER_DIRECTION = 200
COLLEAGUE = "a colleague"
CONNECTED_SCAN_CAP = 2000
COMMENT_MAX = 2000

_KINDS: dict[str, NodeType] = {
    "signals": NodeType.SIGNAL,
    "trends": NodeType.TREND,
    "gaps": NodeType.GAP,
    "content": NodeType.CONTENT_IDEA,
    "drafts": NodeType.CONTENT_DRAFT,
}


def _title_order(node: Node) -> tuple[str, str]:
    return NameNormalizer.title_key(node.title), node.id


class Sort(StrEnum):
    NEWEST = "newest"
    RELEVANCE = "relevance"
    CONNECTED = "connected"
    TITLE = "title"  # A to Z, case and spacing folded (CTR HU-077)


def _strings(value: Any) -> set[str]:
    """A field's text values: the string, or the list (the graph keeps lists to strings). Anything else, a
    number or flag an agent wrote before the field had a type, matches and counts as nothing."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set(value)
    return set()


# The status that ends an output's life, by type: a gap the product now covers, a content idea whose moment
# passed (a deadline gone, a launch already covered). Lists leave these out (CTR HC-126, HC-127).
ENDED_STATUS = {NodeType.GAP: "closed", NodeType.CONTENT_IDEA: "retired"}


def lifecycle_ended(node: Node) -> bool:
    ended = ENDED_STATUS.get(node.type)
    return ended is not None and node.attributes.get("status") == ended


UPCOMING_DAYS = 90
UPCOMING_MAX = 50
FACET_VALUES_MAX = 50


@dataclass(frozen=True, slots=True)
class AttrQuery:
    """Filters and a sort on a list's schema fields. ``filters`` maps a field key to the values any of
    which matches (a list field matches when it holds any of them); filters on different keys all apply."""

    filters: dict[str, frozenset[str]]
    sort: AttributeField | None = None
    descending: bool = False

    def matches(self, node: Node) -> bool:
        return all(_strings(node.attributes.get(key)) & wanted for key, wanted in self.filters.items())

    def order(self, nodes: list[Node]) -> list[Node]:
        """Sorted on the field (enums by rank, dates as written, numbers by value); records without a
        value come last either way, then newest first, so a page never reorders between requests."""
        f = self.sort
        if f is None:
            return sorted(nodes, key=lambda n: (n.created_at, n.id), reverse=True)

        def value(n: Node) -> Any:
            v = n.attributes.get(f.key)
            if f.type is FieldType.ENUM:
                return f.rank_of(v) if isinstance(v, str) and v in f.allowed() else None
            if f.type is FieldType.NUMBER:
                return v if isinstance(v, int | float) and not isinstance(v, bool) else None
            return v if isinstance(v, str) and v else None

        present = [n for n in nodes if value(n) is not None]
        missing = [n for n in nodes if value(n) is None]
        present.sort(key=lambda n: (n.created_at, n.id), reverse=True)
        present.sort(key=value, reverse=self.descending)
        missing.sort(key=lambda n: (n.created_at, n.id), reverse=True)
        return present + missing


@dataclass(frozen=True, slots=True)
class InsightKind:
    node_type: NodeType
    insight_kind: str | None = None

    @classmethod
    def parse(cls, raw: str) -> InsightKind:
        if raw in _KINDS:
            return cls(_KINDS[raw])
        prefix, _, kind = raw.partition(":")
        if prefix == "insight" and INSIGHT_KIND_RE.fullmatch(kind):
            return cls(NodeType.INSIGHT, kind)
        # The valid names, so a caller that guessed ("radars", "facts") can correct itself (MCP test).
        raise ValidationError(
            f"unknown insight kind {raw[:60]!r}; use one of {', '.join(sorted(_KINDS))} or insight:<kind>"
        )


class Vote(StrEnum):
    UP = "up"
    DOWN = "down"


def _offset_cursor(offset: int | None) -> str | None:
    return None if offset is None else base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def _parse_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        text = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, UnicodeDecodeError) as err:
        raise ValidationError("cursor is invalid") from err
    if not re.fullmatch(r"o:[0-9]{1,6}", text):  # isdigit() accepted "\u00b2", which int() rejects
        raise ValidationError("cursor is invalid")
    return int(text[2:])


class InsightService:
    def __init__(
        self, graph: GraphRepository, search: HybridSearch | None, ops_table: Any, clock: Clock | None = None
    ) -> None:
        self._graph = graph
        self._search = search
        self._ops = ops_table
        self._clock = clock or SystemClock()

    # ---- lists ---------------------------------------------------------------------

    def browse(
        self,
        kind: InsightKind,
        *,
        query: str | None = None,
        sort: Sort | None = None,
        cursor: str | None = None,
        principal: Principal | None = None,
        hidden: Callable[[Node], bool] | None = None,
        include_closed: bool = False,
        attrs: AttrQuery | None = None,
    ) -> dict[str, Any]:
        """``hidden`` drops nodes the caller may not read (private-derived outputs for a viewer). A closed gap
        or a retired content idea (attributes.status) is left out unless ``include_closed`` (CTR HC-126/127)."""
        sort = sort or (Sort.RELEVANCE if query else Sort.NEWEST)

        def keep(node: Node) -> bool:
            if not include_closed and lifecycle_ended(node):
                return False
            if attrs is not None and not attrs.matches(node):
                return False
            return hidden is None or not hidden(node)

        if query:
            if self._search is None:
                raise ValidationError("search is not configured")
            hits = self._search.search(
                query, types=[kind.node_type], insight_kind=kind.insight_kind, limit=QUERY_CANDIDATES, hidden=hidden
            )
            nodes = [h.node for h in hits if keep(h.node)]
            if sort is Sort.NEWEST:
                nodes.sort(key=lambda n: n.created_at, reverse=True)
            elif sort is Sort.CONNECTED:
                nodes.sort(key=lambda n: (-n.degree, n.id))
            elif sort is Sort.TITLE:
                nodes.sort(key=_title_order)
            offset = _parse_offset(cursor)
            page = nodes[offset : offset + PAGE_SIZE]
            nxt = offset + PAGE_SIZE if offset + PAGE_SIZE < len(nodes) else None
            body = self._page(page, _offset_cursor(nxt), sort, principal)
            # Only the best QUERY_CANDIDATES matches are paged: say so instead of ending silently.
            body["truncated"] = len(hits) >= QUERY_CANDIDATES
            return body
        if sort is Sort.RELEVANCE:
            raise ValidationError("relevance sort needs a query")
        if attrs is not None and not (sort in (Sort.CONNECTED, Sort.TITLE) and attrs.sort is None):
            # Filters and field sorts read the list through the same bounded scan as the connected and
            # title sorts: correct for lists up to CONNECTED_SCAN_CAP, and says so beyond that.
            scanned = list(self._graph.iterate(kind.node_type, insight_kind=kind.insight_kind, cap=CONNECTED_SCAN_CAP))
            nodes = attrs.order([n for n in scanned if keep(n)])
            offset = _parse_offset(cursor)
            nxt = offset + PAGE_SIZE if offset + PAGE_SIZE < len(nodes) else None
            body = self._page(nodes[offset : offset + PAGE_SIZE], _offset_cursor(nxt), sort, principal)
            body["truncated"] = len(scanned) >= CONNECTED_SCAN_CAP
            body["total"] = len(nodes)
            return body
        if sort in (Sort.CONNECTED, Sort.TITLE):
            nodes = sorted(
                filter(
                    keep, self._graph.iterate(kind.node_type, insight_kind=kind.insight_kind, cap=CONNECTED_SCAN_CAP)
                ),
                key=_title_order if sort is Sort.TITLE else lambda n: (-n.degree, n.id),
            )
            offset = _parse_offset(cursor)
            nxt = offset + PAGE_SIZE if offset + PAGE_SIZE < len(nodes) else None
            return self._page(nodes[offset : offset + PAGE_SIZE], _offset_cursor(nxt), sort, principal)
        # Newest created first: ordered by updated_at, any agent edit refloated an old output (B3-052).
        res = self._graph.newest(kind.node_type, insight_kind=kind.insight_kind, limit=PAGE_SIZE, cursor=cursor)
        return self._page([n for n in res.items if keep(n)], res.cursor, sort, principal)

    def export_nodes(
        self,
        kind: InsightKind,
        *,
        attrs: AttrQuery | None = None,
        hidden: Callable[[Node], bool] | None = None,
        limit: int = CONNECTED_SCAN_CAP,
    ) -> tuple[list[Node], bool]:
        """Every record the list shows with these filters, in its order (newest first by default), up to
        ``limit``; the flag says whether the scan or the limit cut it short."""
        scanned = list(self._graph.iterate(kind.node_type, insight_kind=kind.insight_kind, cap=CONNECTED_SCAN_CAP))
        query = attrs or AttrQuery({})
        nodes = query.order(
            [n for n in scanned if (hidden is None or not hidden(n)) and query.matches(n) and not lifecycle_ended(n)]
        )
        return nodes[:limit], len(scanned) >= CONNECTED_SCAN_CAP or len(nodes) > limit

    def facets(
        self,
        kind: InsightKind,
        schema: AttributeSchema,
        *,
        hidden: Callable[[Node], bool] | None = None,
        days: int = UPCOMING_DAYS,
    ) -> dict[str, Any]:
        """What an overview needs from one list: how many records carry each value of each filterable
        field, and the records whose date fields fall between today and ``days`` ahead (soonest first).
        Read through the same bounded scan as the filtered list, and says when the cap cut it short."""
        scanned = list(self._graph.iterate(kind.node_type, insight_kind=kind.insight_kind, cap=CONNECTED_SCAN_CAP))
        # The same records the list shows: closed gaps are left out there, so they are not counted here.
        nodes = [n for n in scanned if (hidden is None or not hidden(n)) and not lifecycle_ended(n)]
        counts: dict[str, dict[str, int]] = {}
        cut: list[str] = []
        for f in schema.filterable():
            tally = {c.value: 0 for c in f.values}
            for n in nodes:
                for each in _strings(n.attributes.get(f.key)):
                    if not f.values or each in tally:
                        tally[each] = tally.get(each, 0) + 1
            if not f.values and len(tally) > FACET_VALUES_MAX:
                # A free-form field could hold a value per record: only the most common are worth showing.
                tally = dict(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:FACET_VALUES_MAX])
                cut.append(f.key)
            counts[f.key] = tally
        today = self._clock.now().date()
        start, end = today.isoformat(), (today + timedelta(days=days)).isoformat()
        upcoming: list[dict[str, Any]] = []
        for f in (f for f in schema.fields if f.type is FieldType.DATE):
            for n in nodes:
                v = n.attributes.get(f.key)
                if isinstance(v, str) and start <= v <= end:
                    upcoming.append({"field": f.key, "date": v, "node": n.to_public()})
        upcoming.sort(key=lambda u: (u["date"], u["node"]["id"]))
        return {
            "total": len(nodes),
            # How many records were read: the cap, when ``truncated``. ``total`` is what the caller may see.
            "scanned": len(scanned),
            "counts": counts,
            # The free-form fields whose counts keep only the FACET_VALUES_MAX most common values.
            "counts_cut": cut,
            "upcoming": upcoming[:UPCOMING_MAX],
            "days": days,
            "truncated": len(scanned) >= CONNECTED_SCAN_CAP,
        }

    def _page(self, nodes: list[Node], cursor: str | None, sort: Sort, principal: Principal | None) -> dict[str, Any]:
        working = self._working_map([n.id for n in nodes if n.type is NodeType.CONTENT_IDEA], principal)
        items = []
        for n in nodes:
            body = n.to_public()
            if n.type is NodeType.CONTENT_IDEA:
                body["working_on"] = working.get(n.id, [])
            items.append(body)
        return {"items": items, "cursor": cursor, "sort": sort.value, "page_size": PAGE_SIZE}

    # ---- detail --------------------------------------------------------------------

    def detail(
        self, node_id: str, principal: Principal | None = None, hidden: Callable[[Node], bool] | None = None
    ) -> dict[str, Any]:
        node = self._graph.get(node_id)
        if hidden is not None and hidden(node):
            raise NotFoundError(f"node {node_id} not found", {"id": node_id})
        # Each direction gets its own allowance: a node with many outgoing edges used to crowd out every
        # incoming one ("supported by", "addressed by") with no sign anything was missing.
        out_edges = self._graph.edges(node_id, direction="out", limit=DETAIL_EDGES_PER_DIRECTION)
        in_edges = self._graph.edges(node_id, direction="in", limit=DETAIL_EDGES_PER_DIRECTION)
        edges = [*out_edges, *in_edges]
        truncated = DETAIL_EDGES_PER_DIRECTION in (len(out_edges), len(in_edges))
        related_ids = {e.target if e.source == node_id else e.source for e in edges}
        related = self._graph.get_many(related_ids)
        groups: dict[str, list[dict[str, Any]]] = {
            "supports": [],
            "contradicts": [],
            "about": [],
            "addresses": [],
            "addressed_by": [],
            "supported_by": [],
            "drafts": [],
            "other": [],
        }
        for e in edges:
            outgoing = e.source == node_id
            other = related.get(e.target if outgoing else e.source)
            if other is None or (hidden is not None and hidden(other)):
                continue
            entry = {"edge": e.type.value, "direction": "out" if outgoing else "in", "node": _brief(other)}
            if outgoing and e.type is EdgeType.SUPPORTS:
                groups["supports"].append(entry)
            elif outgoing and e.type is EdgeType.CONTRADICTS:
                groups["contradicts"].append(entry)
            elif outgoing and e.type is EdgeType.ABOUT:
                groups["about"].append(entry)
            elif outgoing and e.type is EdgeType.ADDRESSES:
                groups["addresses"].append(entry)
            elif not outgoing and e.type is EdgeType.ADDRESSES:
                groups["addressed_by"].append(entry)
            elif not outgoing and e.type in (EdgeType.SUPPORTS, EdgeType.CONTRADICTS):
                groups["supported_by"].append(entry)
            elif outgoing and e.type is EdgeType.HAS_DRAFT:
                groups["drafts"].append(entry)
            else:
                groups["other"].append(entry)
        body: dict[str, Any] = {"node": node.to_public(), "evidence": groups, "truncated": truncated}
        if node.category is Category.OUTPUT:
            body["feedback"] = self.feedback_summary(node_id, principal)
        if node.type is NodeType.CONTENT_IDEA:
            body["working_on"] = self._working_map([node_id], principal).get(node_id, [])
        return body

    # ---- feedback ------------------------------------------------------------------

    def give_feedback(self, node_id: str, principal: Principal, vote: str, comment: str | None) -> dict[str, Any]:
        node = self._graph.get(node_id)
        if node.category is not Category.OUTPUT:
            raise ValidationError("feedback is only accepted on module outputs")
        try:
            v = Vote(vote)
        except ValueError as err:
            raise ValidationError("vote must be up or down") from err
        text = (comment or "").strip()
        if v is Vote.DOWN and not text:
            raise ValidationError("please say what is wrong when voting down")
        if len(text) > COMMENT_MAX:
            raise ValidationError(f"comment must be at most {COMMENT_MAX} characters")
        now = iso(self._clock.now())
        self._graph.put_while_exists(  # never after the node's delete, which purged the votes (code review r60)
            node_id,
            self._ops.name,
            {
                "pk": f"FEEDBACK#{node_id}",
                "sk": f"VOTE#{principal.subject}",
                "vote": v.value,
                "comment": text,
                "by": principal.subject,
                "email": principal.email or "",
                "at": now,
                "node_type": node.type.value,
                "gsi1pk": f"FEEDBACK#{node.type.value}",
                "gsi1sk": f"{now}#{node_id}",
            },
        )
        return self.feedback_summary(node_id, principal)

    def withdraw_feedback(self, node_id: str, principal: Principal) -> dict[str, Any]:
        """Remove the caller's own vote (another vote cannot replace "no opinion")."""
        self._graph.get(node_id)
        res = self._ops.delete_item(
            Key={"pk": f"FEEDBACK#{node_id}", "sk": f"VOTE#{principal.subject}"}, ReturnValues="ALL_OLD"
        )
        if "Attributes" not in res:
            raise NotFoundError("you have not voted on this node")
        return self.feedback_summary(node_id, principal)

    def feedback_summary(self, node_id: str, principal: Principal | None) -> dict[str, Any]:
        # Every page: past 1 MB of votes (comments are up to 2,000 characters) a single Query gave
        # counts that were simply wrong.
        items = Query.all_items(
            self._ops, KeyConditionExpression=Key("pk").eq(f"FEEDBACK#{node_id}") & Key("sk").begins_with("VOTE#")
        )
        up = sum(1 for i in items if i.get("vote") == "up")
        mine = next((i for i in items if principal is not None and i.get("by") == principal.subject), None)
        return {
            "up": up,
            "down": len(items) - up,
            "mine": {"vote": mine["vote"], "comment": mine.get("comment", "")} if mine else None,
        }

    def feedback_page(
        self, node_type: NodeType, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        partition = f"FEEDBACK#{node_type.value}"
        kwargs: dict[str, Any] = {
            "IndexName": "gsi1",
            "KeyConditionExpression": Key("gsi1pk").eq(partition),
            "ScanIndexForward": False,
            "Limit": max(1, min(limit, 500)),
        }
        start = decode_cursor(cursor, INDEX_KEYS, ("gsi1pk", partition))
        if start:
            kwargs["ExclusiveStartKey"] = start
        res = self._ops.query(**kwargs)
        return [
            {
                "node_id": i["pk"].removeprefix("FEEDBACK#"),
                "vote": i["vote"],
                "comment": i.get("comment", ""),
                "at": i["at"],
                "by": i.get("email") or i["by"],
            }
            for i in res.get("Items", [])
        ], encode_cursor(res.get("LastEvaluatedKey"))

    # ---- working on (content ideas) ---------------------------------------------------

    def set_working(self, node_id: str, principal: Principal, working: bool) -> list[dict[str, str]]:
        node = self._graph.get(node_id)
        if node.type is not NodeType.CONTENT_IDEA:
            raise ValidationError("only content ideas can be marked as being worked on")
        key = {"pk": f"WORKING#{node_id}", "sk": f"USER#{principal.subject}"}
        if working:
            self._graph.put_while_exists(  # never after the idea's delete, which purged the marks (code review r60)
                node_id,
                self._ops.name,
                {
                    **key,
                    "email": principal.email or principal.subject,
                    "since": iso(self._clock.now()),
                    # Indexed by user, so deleting an account can remove its marks: they used to
                    # outlive it and show the idea as being worked on by "a colleague" forever.
                    "gsi1pk": f"WORKING#USER#{principal.subject}",
                    "gsi1sk": node_id,
                },
            )
        else:
            self._ops.delete_item(Key=key)
        return self._working_map([node_id], principal).get(node_id, [])

    def _working_map(self, node_ids: list[str], principal: Principal | None) -> dict[str, list[dict[str, str]]]:
        """Who is working on each idea. A colleague's email and subject are shown only to themself and to
        administrators; everyone else sees that a colleague is on it."""
        out: dict[str, list[dict[str, str]]] = {}
        for nid in node_ids:
            res = self._ops.query(KeyConditionExpression=Key("pk").eq(f"WORKING#{nid}"))
            rows = []
            for i in res.get("Items", []):
                user = i["sk"].removeprefix("USER#")
                visible = principal is not None and (principal.is_admin or principal.subject == user)
                rows.append(
                    {
                        "user": user if visible else "",
                        "email": i.get("email", "") if visible else COLLEAGUE,
                        "since": i.get("since", ""),
                    }
                )
            out[nid] = rows
        return out

    def purge(self, node_id: str) -> int:
        """Remove a deleted node's feedback votes and working-on marks (they hold emails)."""
        removed = 0
        for prefix in ("FEEDBACK#", "WORKING#"):
            # Every page: a single Query left rows (with emails) behind on a busy node.
            # Consistent: a vote written just before the node was flagged must be seen (security review r62).
            for item in Query.all_items(
                self._ops, KeyConditionExpression=Key("pk").eq(f"{prefix}{node_id}"), ConsistentRead=True
            ):
                self._ops.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                removed += 1
        return removed

    def index_working_marks(
        self, page_size: int = 500, start: dict[str, Any] | None = None, on_page: PageHook | None = None
    ) -> int:
        """Add the by-user index key to working-on marks written before it existed, so deleting their
        user removes them too (CR2-12). Returns how many marks were indexed."""
        indexed = 0
        kwargs: dict[str, Any] = {
            "Limit": page_size,
            "FilterExpression": "begins_with(pk, :p) AND attribute_not_exists(gsi1pk)",
            "ExpressionAttributeValues": {":p": "WORKING#"},
            **({"ExclusiveStartKey": start} if start else {}),
        }
        while True:
            res = self._ops.scan(**kwargs)
            for item in res.get("Items", []):
                subject = str(item["sk"]).removeprefix("USER#")
                self._ops.update_item(
                    Key={"pk": item["pk"], "sk": item["sk"]},
                    UpdateExpression="SET gsi1pk = :u, gsi1sk = :n",
                    ConditionExpression="attribute_exists(pk)",
                    ExpressionAttributeValues={
                        ":u": f"WORKING#USER#{subject}",
                        ":n": str(item["pk"]).removeprefix("WORKING#"),
                    },
                )
                indexed += 1
            nxt = res.get("LastEvaluatedKey")
            if (on_page is not None and not on_page(nxt)) or not nxt:
                return indexed
            kwargs["ExclusiveStartKey"] = nxt

    def working_marks_of(self, subject: str) -> int:
        """Remove every working-on mark a user left. Without this an offboarded account kept ideas
        marked as being worked on by "a colleague" forever."""
        removed = 0
        for item in Query.all_items(
            self._ops,
            IndexName="gsi1",
            KeyConditionExpression=Key("gsi1pk").eq(f"WORKING#USER#{subject}"),
        ):
            self._ops.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
            removed += 1
        return removed


def _brief(node: Node) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": node.id,
        "type": node.type.value,
        "title": node.title,
        "summary": node.summary[:500],
        "updated_at": node.updated_at,
    }
    if node.evidence_url:
        body["evidence_url"] = node.evidence_url
    if node.insight_kind:
        body["insight_kind"] = node.insight_kind
    return body
