# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Append-only audit log of every mutation (who, what, target, when).

Rows live in the ops table partitioned by UTC day (``AUDIT#<yyyy-mm-dd>``) so a day's activity
is one query; details are size-capped and never contain secrets or document content.
"""

from __future__ import annotations

import json
import re
from typing import Any

from boto3.dynamodb.conditions import Key

from insights_radar.core.clock import Clock, SystemClock, iso
from insights_radar.core.errors import ValidationError
from insights_radar.core.ids import IdGenerator
from insights_radar.graph.repository import TABLE_KEYS, decode_cursor, encode_cursor
from insights_radar.identity.principal import Principal

DETAILS_MAX = 2000
AUDIT_TTL_DAYS = 400
_LIST_MAX = 10
_STRING_MAX = 200
_KEYS_MAX = 30
_DEPTH_MAX = 4


def _shrink(value: Any, depth: int = 0) -> Any:
    """``value`` with its strings clipped and its lists and maps truncated, so what is serialised is
    already small enough. Cutting the serialised text instead produced invalid JSON, and ``page``
    reads every row with ``json.loads``: one long row made a whole day's audit log a 500."""
    if isinstance(value, str):
        return value[:_STRING_MAX]
    if isinstance(value, list):
        kept = [_shrink(v, depth + 1) for v in value[:_LIST_MAX]]
        return [*kept, f"... {len(value) - _LIST_MAX} more"] if len(value) > _LIST_MAX else kept
    if isinstance(value, dict):
        if depth >= _DEPTH_MAX:
            return f"... {len(value)} keys"
        return {str(k)[:60]: _shrink(v, depth + 1) for k, v in list(value.items())[:_KEYS_MAX]}
    return value


def _details_json(details: dict[str, Any] | None) -> str:
    body = json.dumps(details or {}, default=str, sort_keys=True)
    if len(body) <= DETAILS_MAX:
        return body
    body = json.dumps(_shrink(details or {}), default=str, sort_keys=True)
    if len(body) <= DETAILS_MAX:
        return body
    # Still too large (very many keys): keep a valid, readable marker rather than a cut string.
    return json.dumps({"truncated": True, "keys": sorted(str(k)[:40] for k in (details or {}))[:20]})


# A list longer than ``_LIST_MAX`` is carried on continuation rows rather than cut to "... N more":
# offboarding a user removed from 40 radars recorded ten slugs (B3-044). Bounded, so one call cannot
# write an unbounded number of rows.
CONTINUATION_ROWS_MAX = 50


def _split(details: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``details`` with each long top-level list cut to its first ``_LIST_MAX`` items, and the rest as
    continuation rows (``{key: chunk, "part": n, "of": total}``)."""
    body = dict(details or {})
    long_lists = {k: v for k, v in body.items() if isinstance(v, list) and len(v) > _LIST_MAX}
    rows: list[dict[str, Any]] = []
    for key, values in long_lists.items():
        body[key] = values[:_LIST_MAX]
        for start in range(_LIST_MAX, len(values), _LIST_MAX):
            rows.append({key: values[start : start + _LIST_MAX]})
    rows = rows[:CONTINUATION_ROWS_MAX]
    if rows:
        body["continued_rows"] = len(rows)
    return body, [{**row, "part": n, "of": len(rows)} for n, row in enumerate(rows, 1)]


class AuditLog:
    def __init__(self, table: Any, clock: Clock | None = None, ids: IdGenerator | None = None) -> None:
        self._t = table
        self._clock = clock or SystemClock()
        self._ids = ids or IdGenerator(self._clock)

    def record(self, principal: Principal, action: str, target: str, details: dict[str, Any] | None = None) -> None:
        self.record_actor(principal.actor, principal.email or "", action, target, details)

    def record_actor(
        self, actor: str, email: str, action: str, target: str, details: dict[str, Any] | None = None
    ) -> None:
        """A row for a change no signed-in user made (autopilot, a module run). A long list in the
        details continues on ``<action>.continued`` rows with the same target."""
        now = self._clock.now()
        main, continued = _split(details)
        for row_action, row in [(action, main), *((f"{action}.continued", c) for c in continued)]:
            self._t.put_item(
                Item={
                    "pk": f"AUDIT#{now.strftime('%Y-%m-%d')}",
                    "sk": f"{iso(now)}#{self._ids.uuid7()}",
                    "kind": "audit",
                    "actor": actor,
                    "email": email,
                    "action": row_action,
                    "target": target[:300],
                    "details": _details_json(row),
                    "at": iso(now),
                    "ttl": int(now.timestamp()) + AUDIT_TTL_DAYS * 86400,  # the tables' TTL attribute
                }
            )

    def page(self, date: str, limit: int = 200, cursor: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        """One page of a day's rows, newest first, and the cursor for the next (a busy day has more than
        any single page holds)."""
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
            raise ValidationError("date must be YYYY-MM-DD")
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(f"AUDIT#{date}"),
            "ScanIndexForward": False,
            "Limit": max(1, min(limit, 1000)),
        }
        start = decode_cursor(cursor, TABLE_KEYS, ("pk", f"AUDIT#{date}"))
        if start:
            kwargs["ExclusiveStartKey"] = start
        res = self._t.query(**kwargs)
        return [
            {
                # Every field is read defensively: one malformed row must not make the whole day's
                # page a 500 (which is how a long details blob used to hide a day's audit log).
                "at": str(i.get("at", "")),
                "actor": str(i.get("actor", "")),
                "email": str(i.get("email", "")),
                "action": str(i.get("action", "")),
                "target": str(i.get("target", "")),
                "details": _parse_details(i.get("details")),
            }
            for i in res.get("Items", [])
        ], encode_cursor(res.get("LastEvaluatedKey"))


def _parse_details(raw: Any) -> Any:
    """A row's details, tolerating one written before they were capped structurally: a bad row shows
    as text instead of failing the whole day's page."""
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"unparsed": str(raw)[:DETAILS_MAX]}
    return parsed if isinstance(parsed, dict) else {"unparsed": str(raw)[:DETAILS_MAX]}
