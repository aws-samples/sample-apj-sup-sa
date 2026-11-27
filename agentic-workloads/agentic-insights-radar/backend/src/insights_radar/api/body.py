# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Typed extraction of request-body fields with uniform 400 errors."""

from __future__ import annotations

from typing import Any

from insights_radar.core.errors import ValidationError


class Body:
    def __init__(self, data: dict[str, Any]) -> None:
        self._d = data

    @property
    def raw(self) -> dict[str, Any]:
        return self._d

    def has(self, key: str) -> bool:
        return key in self._d

    def text(self, key: str, *, required: bool = True, default: str = "", max_len: int = 10_000) -> str:
        v = self._d.get(key)
        if v is None:
            if required:
                raise ValidationError(f"{key} is required")
            return default
        if not isinstance(v, str):
            raise ValidationError(f"{key} must be a string")
        if len(v) > max_len:
            raise ValidationError(f"{key} must be at most {max_len} characters")
        return v

    def opt_text(self, key: str, max_len: int = 10_000) -> str | None:
        if self._d.get(key) is None:
            return None
        return self.text(key, max_len=max_len)

    def integer(
        self, key: str, *, required: bool = True, default: int = 0, low: int | None = None, high: int | None = None
    ) -> int:
        v = self._d.get(key)
        if v is None:
            if required:
                raise ValidationError(f"{key} is required")
            return default
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValidationError(f"{key} must be an integer")
        if (low is not None and v < low) or (high is not None and v > high):
            raise ValidationError(f"{key} must be between {low} and {high}")
        return v

    def flag(self, key: str, *, required: bool = True, default: bool = False) -> bool:
        v = self._d.get(key)
        if v is None:
            if required:
                raise ValidationError(f"{key} is required")
            return default
        if not isinstance(v, bool):
            raise ValidationError(f"{key} must be true or false")
        return v

    def texts(self, key: str, *, required: bool = False, max_items: int = 200, max_len: int = 1000) -> list[str]:
        v = self._d.get(key)
        if v is None:
            if required:
                raise ValidationError(f"{key} is required")
            return []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValidationError(f"{key} must be a list of strings")
        if len(v) > max_items:
            raise ValidationError(f"{key} must have at most {max_items} items")
        if any(len(x) > max_len for x in v):
            raise ValidationError(f"each {key} item must be at most {max_len} characters")
        return list(v)

    def mapping(self, key: str, *, required: bool = True) -> dict[str, Any]:
        v = self._d.get(key)
        if v is None:
            if required:
                raise ValidationError(f"{key} is required")
            return {}
        if not isinstance(v, dict):
            raise ValidationError(f"{key} must be an object")
        return v

    def items(self, key: str, *, required: bool = True, max_items: int = 1000) -> list[Any]:
        v = self._d.get(key)
        if v is None:
            if required:
                raise ValidationError(f"{key} is required")
            return []
        if not isinstance(v, list):
            raise ValidationError(f"{key} must be a list")
        if len(v) > max_items:
            raise ValidationError(f"{key} must have at most {max_items} items")
        return v

    def only(self, *allowed: str) -> None:
        extra = set(self._d) - set(allowed)
        if extra:
            raise ValidationError(f"unknown fields: {', '.join(sorted(extra))}")
