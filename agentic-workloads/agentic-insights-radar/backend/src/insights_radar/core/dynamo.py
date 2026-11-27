# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""DynamoDB query helpers shared by the repositories."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")
# What a migration is told after each page: the key to resume from (None when it has finished). It
# answers whether to go on.
PageHook = Callable[[dict[str, Any] | None], bool]


class Query:
    @staticmethod
    def all_items(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Every item a query matches. One response stops at 1 MB, so a single call can silently drop
        the rest of a large partition."""
        items: list[dict[str, Any]] = []
        while True:
            res = table.query(**kwargs)
            items.extend(res.get("Items", []))
            if "LastEvaluatedKey" not in res:
                return items
            kwargs = {**kwargs, "ExclusiveStartKey": res["LastEvaluatedKey"]}

    @staticmethod
    def count(table: Any, **kwargs: Any) -> int:
        total = 0
        while True:
            res = table.query(Select="COUNT", **kwargs)
            total += int(res.get("Count", 0))
            if "LastEvaluatedKey" not in res:
                return total
            kwargs = {**kwargs, "ExclusiveStartKey": res["LastEvaluatedKey"]}


class MigrationLedger:
    """Where an unfinished data migration stopped, so the next deploy resumes it.

    The seed step used to run every migration from the start on every deploy: full scans of both
    tables each time, long enough on a large graph to exceed the seed timeout and fail every deploy,
    starting over the next time (B3-019). Each migration now records its ``LastEvaluatedKey`` after
    every page in the ops table, so one that ran out of time resumes where it stopped.

    There is deliberately no permanent "done" mark (CR3-01): every migration the seed runs is an
    idempotent re-key that the previous release can undo after a rollback (it writes the old keys,
    no listing items, the legacy tombstone item). CloudFormation never rolls an ops-table item back,
    so a done mark would skip the repair for good. A finished migration deletes its state instead, and
    the next deploy checks the stored keys again (P3-003). A state item an older build left marked
    ``done`` is ignored the same way.
    """

    def __init__(self, table: Any) -> None:
        self._t = table

    @staticmethod
    def _key(name: str) -> dict[str, str]:
        return {"pk": f"MIGRATION#{name}", "sk": "STATE"}

    def cursor(self, name: str) -> dict[str, Any] | None:
        """Where the migration stopped last time, or None to start from the beginning."""
        item = self._t.get_item(Key=self._key(name), ConsistentRead=True).get("Item") or {}
        cursor = item.get("cursor")
        return dict(cursor) if isinstance(cursor, dict) else None

    def _record(self, name: str, cursor: dict[str, Any] | None) -> None:
        if cursor is None:
            self._t.delete_item(Key=self._key(name))
        else:
            self._t.put_item(Item={**self._key(name), "kind": "migration", "cursor": cursor})

    def run(
        self,
        name: str,
        migrate: Callable[[dict[str, Any] | None, PageHook], T],
        keep_going: Callable[[], bool],
    ) -> T:
        """Run ``migrate(start, on_page)`` from where it last stopped. ``keep_going`` is asked after
        each page (the seed's remaining time), so a long migration stops cleanly with its place
        recorded rather than being killed half-way."""

        def on_page(next_key: dict[str, Any] | None) -> bool:
            self._record(name, next_key)
            return next_key is None or keep_going()

        return migrate(self.cursor(name), on_page)
