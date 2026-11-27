# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Ops-table persistence for the deployment profile: one item, ``PROFILE/CURRENT``."""

from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal
from typing import Any

from botocore.exceptions import ChecksumError, ClientError, HTTPClientError, IncompleteReadError
from botocore.exceptions import ConnectionError as BotoConnectionError

from insights_radar.core.clock import Clock, SystemClock, iso
from insights_radar.core.errors import ConflictError, ValidationError
from insights_radar.graph.repository import _from_ddb, _to_ddb
from insights_radar.profile.model import Profile, parse_profile, with_meta

log = logging.getLogger(__name__)

_KEY = {"pk": "PROFILE", "sk": "CURRENT"}


class ProfileRepository:
    """Read on the unauthenticated ``/api/config``, so reads are cached per container. The profile is
    display text: an unreadable or corrupt item falls back to the built-in text rather than failing the
    route (the app still works; it only reads as the default deployment)."""

    CACHE_SECONDS = 60

    def __init__(self, table: Any, clock: Clock | None = None) -> None:
        self._t = table
        self._clock = clock or SystemClock()
        self._cache: tuple[float, Profile] | None = None

    def get(self, cached: bool = False) -> Profile:
        now = self._clock.now().timestamp()
        if cached and self._cache is not None and now - self._cache[0] <= self.CACHE_SECONDS:
            return self._cache[1]
        profile = self._read()
        self._cache = (now, profile)
        return profile

    def _read(self, strict: bool = False, read_errors_raise: bool = False) -> Profile:
        """The stored profile. Not ``strict``: a failed read or an unreadable item falls back to the built-in text.
        ``strict``, for a decision that must not fall back (the sample-data gate, code review r17): a failed read
        raises and an unreadable item is refused. ``read_errors_raise``: a failed read raises but an unreadable
        item still falls back (a save, which must not write over what it could not read; code review r20)."""
        return self._load(strict, read_errors_raise)[1]

    def _load(self, strict: bool = False, read_errors_raise: bool = False) -> tuple[dict[str, Any] | None, Profile]:
        """The raw item (None when there is none) and the profile read from it."""
        try:
            item = self._t.get_item(Key=_KEY, ConsistentRead=True).get("Item")
        except READ_ERRORS:  # a timeout or dropped connection too (code review r23)
            if strict or read_errors_raise:
                raise
            log.exception("profile.read_failed")
            return None, Profile()
        if not item:
            return None, Profile()
        doc = item.get("doc")
        try:
            if not isinstance(doc, dict):
                # An item with no readable doc is not "never configured" (code reviews r19, r20).
                raise ValidationError("the stored profile has no doc")
            # DynamoDB hands numbers back as Decimal; the validator wants plain ints.
            parsed = _raw_unknown(parse_profile(_from_ddb(doc), stored=True), doc)
        except ValueError as err:
            if strict:
                raise ConflictError("the deployment profile cannot be read; fix it under Administration first") from err
            # The built-in text is shown, but under the stored version: an administrator (or the seed) can
            # then replace the item. Version 0 sent "create only" against an item that exists, forever.
            log.exception("profile.stored_invalid")
            parsed = Profile(stored_invalid=True)
        version = _whole(item.get("version", 0))
        if version is None:
            # Not a whole number ("5", 2.5, a string): unreadable too, but the doc and its kept fields stay, and a
            # save matches the raw stored value, so it can be repaired (code reviews r21, r22).
            if strict:
                raise ConflictError("the deployment profile cannot be read; fix it under Administration first")
            log.warning("profile.stored_invalid_version")
            parsed, version = replace(parsed, stored_invalid=True), 0
        return item, with_meta(parsed, version, item.get("updated_by"), item.get("updated_at"))

    def get_for_write(self) -> Profile:
        """The stored profile for a decision a write depends on (the seed's): a failed read raises, so it is never
        mistaken for "never written" (code review r24); an unreadable item still reads as stored_invalid."""
        return self._read(read_errors_raise=True)

    def get_strict(self) -> Profile:
        """The stored profile, read now; raises rather than falling back to the defaults."""
        return self._read(strict=True)

    def put(
        self, doc: Any, actor: str, expected_version: int | None, drop_unknown: frozenset[str] = frozenset()
    ) -> Profile:
        """Replace the profile. ``expected_version`` guards against two admins overwriting each other;
        None writes unconditionally (the deploy-time seed). ``drop_unknown`` names stored fields this code does
        not know that the admin wants gone (``Profile.unknown_keys``); a name that is not stored is refused."""
        stored, current = self._load(read_errors_raise=True)
        if expected_version is not None and expected_version != current.version:
            # Before the drop names are checked: a stale editor is told to reload (code review r20).
            raise ConflictError(
                "the profile was changed by someone else; reload it and try again",
                {"current_version": current.version},
            )
        # What a newer release stored that this code does not know survives an admin save (code review r18).
        parsed = parse_profile(doc).keeping_unknown(current, drop_unknown)
        # At the top of the range the count starts again, so a save never writes a version it cannot read back.
        version = current.version + 1 if current.version < VERSION_MAX else 1
        now = iso(self._clock.now())
        # Kept unknown values are the raw item's (Decimal already); _to_ddb is a safety net for any float.
        item = {**_KEY, "doc": _to_ddb(parsed.to_item()), "version": version, "updated_by": actor, "updated_at": now}
        kwargs: dict[str, Any] = {"Item": item}
        if expected_version is not None:
            # The item must still be the one read: matched on its raw version, so a malformed one can be replaced
            # and nothing written in between is (code review r22).
            if stored is None:
                kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
            elif "version" not in stored:
                kwargs["ConditionExpression"] = "attribute_exists(pk) AND attribute_not_exists(#v)"
                kwargs["ExpressionAttributeNames"] = {"#v": "version"}
            elif stored["version"] is None:
                # A NULL never compares equal; match its type instead (code review r23).
                kwargs["ConditionExpression"] = "attribute_type(#v, :t)"
                kwargs["ExpressionAttributeNames"] = {"#v": "version"}
                kwargs["ExpressionAttributeValues"] = {":t": "NULL"}
            else:
                kwargs["ConditionExpression"] = "#v = :v"
                kwargs["ExpressionAttributeNames"] = {"#v": "version"}
                kwargs["ExpressionAttributeValues"] = {":v": stored["version"]}
        try:
            self._t.put_item(**kwargs)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ConflictError(
                    "the profile was changed by someone else; reload it and try again",
                    {"current_version": current.version},
                ) from e
            raise
        saved = with_meta(parsed, version, actor, now)
        self._cache = (self._clock.now().timestamp(), saved)
        return saved


VERSION_MAX = 10**12  # the admin API's expected_version accepts 0..VERSION_MAX

# Failures that pass: a timeout, a dropped connection, a reply damaged in transit (code review r25).
# Not every BotoCoreError: a missing credential or a bad
# parameter is a fault to surface, not a reason to show the built-in text (code review r24).
NETWORK_ERRORS: tuple[type[Exception], ...] = (HTTPClientError, BotoConnectionError, ChecksumError, IncompleteReadError)
# A service refusal or a network failure: one tuple, so mypy can check the except clauses that catch both.
READ_ERRORS: tuple[type[Exception], ...] = (ClientError, *NETWORK_ERRORS)


def _whole(value: Any) -> int | None:
    """A stored version as an int, or None when it is not a whole number in 0..VERSION_MAX (bools and text
    included): one the admin API could not send back is unreadable too, so it can be repaired (code review r23)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        value = int(value)
    if isinstance(value, int) and 0 <= value <= VERSION_MAX:
        return value
    return None


def _raw_unknown(parsed: Profile, doc: dict[str, Any]) -> Profile:
    """``parsed`` with its kept unknown values taken from the raw item, so they are written back exactly as a newer
    release stored them (no Decimal to float to Decimal rounding; code review r20)."""
    if not parsed.unknown:
        return parsed
    raw: dict[str, Any] = {k: doc[k] for k in parsed.unknown if k != "sections"}
    extra = parsed.unknown.get("sections")
    if isinstance(extra, dict):
        raw["sections"] = {k: doc["sections"][k] for k in extra}
    return replace(parsed, unknown=raw)
