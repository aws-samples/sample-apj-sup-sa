"""Shared test fixtures for the HVI CDK synthesis tests.

Puts ``infra/cdk`` on ``sys.path`` (so ``import stacks....`` resolves the same
way it does for ``app.py``) and exposes a factory fixture for building an
``App`` with a given context dict.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import aws_cdk as cdk
import pytest

# infra/cdk/ (parent of this tests/ dir) must be importable as the app root so
# `stacks` resolves the same way it does for app.py.
_CDK_ROOT = Path(__file__).resolve().parent.parent
if str(_CDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_CDK_ROOT))


@pytest.fixture
def make_app() -> Callable[..., cdk.App]:
    """Return a factory building a CDK ``App`` seeded with ``hvi:*`` context."""

    def _factory(context: dict | None = None) -> cdk.App:
        return cdk.App(context=dict(context or {}))

    return _factory
