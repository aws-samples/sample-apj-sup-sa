# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Watched records and the "what's new" digest (CTR subscriptions and subscription-digest, HA-120/122).

A user watches graph records (an entity such as a regulation, a company or a product). The digest
lists what changed since the user last read it: the new conclusions (signals, trends, gaps, ideas),
and for each watched record the evidence and conclusions about it that arrived since. It is read in
the app or through MCP; nothing is emailed, so it needs no mail identity and sends nothing outward.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from boto3.dynamodb.conditions import Key

from insights_radar.core.clock import Clock, iso, parse_iso
from insights_radar.core.dynamo import Query
from insights_radar.core.errors import ValidationError
from insights_radar.graph.ask import LINKED
from insights_radar.graph.model import Node, node_type_of
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.search import nothing_hidden
from insights_radar.identity.policy import Action, Policy
from insights_radar.identity.principal import Principal
from insights_radar.modules.model import PRIVATE_OUTPUT_TYPES, private_hider

MAX_WATCHES = 50
DIGEST_TYPES = (NodeType.SIGNAL, NodeType.TREND, NodeType.GAP, NodeType.CONTENT_IDEA)
PER_TYPE = 20
PER_WATCH = 10
WATCH_PER_TYPE = 25  # the newest links of each kind of record about a watched one
FIRST_DIGEST_DAYS = 7
MAX_DIGEST_DAYS = 90
MAX_PAGES = 5  # pages of a list read past records the reader may not see


@dataclass(frozen=True, slots=True)
class ReaderView:
    """What one reader may see of a digest: the records hidden from them, and the lists they never see."""

    hidden: Callable[[Node], bool]
    unseen: frozenset[NodeType]


def reader_view(reader: Principal, private_writers: Callable[[], Iterable[str]]) -> ReaderView:
    """The one rule for a digest's reader, in the app and in a delivery alike: a reader without ``product.read``
    (a viewer) does not see the outputs derived from private product documents (gaps, content ideas, drafts)
    or anything stamped private, by the same rule the agent tools' hider applies (B3-018)."""
    if Policy.allows(reader, Action.PRODUCT_READ):
        return ReaderView(nothing_hidden, frozenset())
    return ReaderView(private_hider(frozenset(private_writers())), PRIVATE_OUTPUT_TYPES)


def custom_lists(modules: Iterable[Any]) -> dict[str, str]:
    """Each custom module's insight kind and its list label: the digest lists their outputs too."""
    return {m.insight_kind: m.nav_label for m in modules if m.insight_kind}


class DigestService:
    def __init__(self, table: Any, graph: GraphRepository, clock: Clock) -> None:
        self._t = table
        self._graph = graph
        self._clock = clock

    # ---- watches --------------------------------------------------------------------------

    def watches(self, subject: str) -> list[str]:
        rows = Query.all_items(
            self._t, KeyConditionExpression=Key("pk").eq(f"USER#{subject}") & Key("sk").begins_with("WATCH#")
        )
        return [str(r["sk"]).removeprefix("WATCH#") for r in rows]

    def set_watch(self, subject: str, node_id: str, on: bool) -> bool:
        key = {"pk": f"USER#{subject}", "sk": f"WATCH#{node_id}"}
        if not on:
            self._t.delete_item(Key=key)
            return False
        current = self.watches(subject)
        if node_id not in current and len(current) >= MAX_WATCHES:
            # Watches on records since removed do not count: drop them, then check again.
            live = self._graph.get_many(current)
            for gone in (i for i in current if i not in live):
                self._t.delete_item(Key={"pk": f"USER#{subject}", "sk": f"WATCH#{gone}"})
            current = [i for i in current if i in live]
        if node_id not in current and len(current) >= MAX_WATCHES:
            raise ValidationError(f"at most {MAX_WATCHES} watched records: stop watching one first")
        self._t.put_item(Item={**key, "kind": "watch", "at": iso(self._clock.now())})
        return True

    # ---- digest ---------------------------------------------------------------------------

    def last_read(self, subject: str) -> str | None:
        item = self._t.get_item(Key={"pk": f"USER#{subject}", "sk": "DIGEST#read"}).get("Item")
        return str(item["at"]) if item else None

    def mark_read(self, subject: str, upto: str | None = None) -> str:
        """Read up to ``upto``: the ``until`` of the digest on screen (a time, or a record's
        ``<time>#<id>`` when a list was cut), so what came after it is kept for the next digest. Never
        later than now, and never back before an earlier read."""
        clock = self._clock.now()
        now = iso(clock)
        at = now
        if upto:
            stamp, _, ident = upto.partition("#")
            try:
                when = parse_iso(stamp)
            except ValueError as err:
                raise ValidationError("upto must be an ISO 8601 time") from err
            # Stored in the index's own form (UTC, milliseconds) so it compares with record keys; a
            # record id is kept only when it is one and its time has passed (code review r10).
            at = iso(min(when, clock))
            if ident and when <= clock and _is_node_id(ident):
                at = f"{at}#{ident}"
        before = self.last_read(subject)

        def place(point: str) -> str:  # a bare time reads as the end of its millisecond
            return point if "#" in point else f"{point}#~"

        if before and place(before) > place(at):
            return before  # a digest loaded earlier never moves the read point back (code review r8)
        self._t.put_item(Item={"pk": f"USER#{subject}", "sk": "DIGEST#read", "kind": "digest_read", "at": at})
        return at

    def digest(
        self,
        subject: str,
        *,
        since: str | None,
        hidden: Callable[[Node], bool],
        custom: dict[str, str] | None = None,
        unseen: frozenset[NodeType] = frozenset(),
        read_point: tuple[str | None] | None = None,
        watches: bool = True,
        off_kinds: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """What changed after ``since`` (default: the last read, or a week back for a first digest).
        ``custom`` maps each custom module's insight kind to its list label: their outputs are listed too.
        ``read_point`` replaces the in-app read point with another one (a delivery's own cursor, None for a
        delivery that never sent), so reading in the app and a delivery never move each other's place.
        ``watches`` False leaves the reader's watched records out (a Slack channel post: one person's watch list
        is not for everyone in the channel). The records new about a watched one leave out the types in ``unseen``
        and the custom insight kinds in ``off_kinds`` too: a list the deployment hides is not delivered by way of a
        watched record (scan cycle 3)."""
        now = self._clock.now()
        floor = now - timedelta(days=MAX_DIGEST_DAYS)
        if since:
            try:
                start = parse_iso(since)
            except ValueError as err:
                raise ValidationError("since must be an ISO 8601 time") from err
        read = None if since else read_point[0] if read_point is not None else self.last_read(subject)
        if not since:
            start = parse_iso(read.partition("#")[0]) if read else now - timedelta(days=FIRST_DIGEST_DAYS)
        start = min(max(start, floor), now)  # never before the 90-day floor, never after now
        after = iso(start)
        # The window runs from the read point, a record key when the last digest was cut inside a
        # millisecond, to the end of the first list cut at PER_TYPE (code review r8/r9).
        after_key = read if read and "#" in read and read.partition("#")[0] >= after else f"{after}#~"
        until_key = f"{iso(now)}#~"
        cut: set[str] = set()
        pages: dict[str, list[Node]] = {}
        labels: dict[str, str] = {}
        # A list of a type the reader can never see is not read at all: it would only be paged through
        # and could not be shown (code review r11).
        lists = [(t.value, t, None) for t in DIGEST_TYPES if t not in unseen] + [
            (f"insight:{kind}", NodeType.INSIGHT, kind) for kind in sorted(custom or {})
        ]
        for key, node_type, kind in lists:
            # Oldest first from the read point, so a list cut at PER_TYPE ends the window there and
            # marking it read keeps the rest for the next digest. Pages thinned by records the reader
            # may not see are read on, so a list of hidden records alone never stops the window (r10).
            visible: list[Node] = []
            cursor: str | None = None
            last: Node | None = None
            for _ in range(MAX_PAGES):
                page = self._graph.recent(
                    node_type, insight_kind=kind, after=after_key, limit=PER_TYPE * 3, oldest_first=True, cursor=cursor
                )
                visible += [n for n in page.items if not hidden(n)]
                last = page.items[-1] if page.items else last
                cursor = page.cursor
                if not cursor or len(visible) > PER_TYPE:
                    break
            if len(visible) > PER_TYPE:
                cut.add(key)
                until_key = min(until_key, _key(visible[PER_TYPE - 1]))
            elif cursor and last is not None:  # more hidden records than MAX_PAGES pages hold
                cut.add(key)
                # Cut at the end of that record's millisecond: the reader may not see it, so its id
                # is not handed out (code review r11).
                until_key = min(until_key, f"{last.updated_at}#~")
            pages[key] = visible[:PER_TYPE]
            if kind:
                labels[key] = (custom or {})[kind]
        new = {k: [_row(n) for n in reversed(v) if _key(n) <= until_key] for k, v in pages.items()}
        more = {k: k in cut for k in pages}
        watched: list[dict[str, Any]] = []
        nodes = self._graph.get_many(self.watches(subject)) if watches else {}
        for node in sorted(nodes.values(), key=lambda n: n.title.lower()):
            if hidden(node):
                continue
            edges = self._graph.newest_links(
                node.id, direction="in", edge_types=[EdgeType.ABOUT], node_types=LINKED, per_type=WATCH_PER_TYPE
            )
            linked = self._graph.get_many([e.source for e in edges])
            fresh = sorted(
                (
                    n
                    for n in linked.values()
                    if after_key < _key(n) <= until_key
                    and not hidden(n)
                    and n.type not in unseen
                    and (n.insight_kind is None or n.insight_kind not in off_kinds)
                ),
                key=lambda n: n.updated_at,
                reverse=True,
            )
            watched.append({"node": _row(node), "new": [_row(n) for n in fresh[:PER_WATCH]], "count": len(fresh)})
        total = sum(len(v) for v in new.values()) + sum(int(w["count"]) for w in watched)
        return {
            "since": after,
            "until": until_key if cut else iso(now),
            "total": total,
            "new": new,
            "more": more,
            "labels": labels,
            "watched": watched,
        }


def _is_node_id(value: str) -> bool:
    try:
        node_type_of(value)
    except ValueError:
        return False
    return True


def _key(n: Node) -> str:
    """A record's place in the time order, as the graph's index sorts it: ``<updated_at>#<id>``."""
    return f"{n.updated_at}#{n.id}"


def _row(n: Node) -> dict[str, Any]:
    return {
        "id": n.id,
        "type": n.type.value,
        "title": n.title,
        "summary": n.summary[:300],
        "evidence_url": n.evidence_url,
        "updated_at": n.updated_at,
    }
