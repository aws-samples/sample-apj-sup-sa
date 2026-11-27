# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Synthesise the stack once per configuration, against stand-in build output."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Annotations, Template

from insights_radar_infra import agents, backend, frontend
from insights_radar_infra.application import Application

ENV = cdk.Environment(account="111122223333", region="us-east-1")
# The feature flags in cdk.json change what CDK generates, so synthesise with them as the CLI does.
CONTEXT = json.loads((Path(__file__).parents[1] / "cdk.json").read_text())["context"]


@dataclass(frozen=True)
class Synth:
    template: Template
    annotations: Annotations

    def resources(self, type_: str) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self.template.find_resources(type_).items()}

    def props(self, type_: str) -> list[dict[str, Any]]:
        return [r.get("Properties", {}) for r in self.resources(type_).values()]


@pytest.fixture(scope="session", autouse=True)
def bundles(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """``deployment/build.py`` output, replaced with one-file directories so tests need no build."""
    root = tmp_path_factory.mktemp("build")
    saved = backend.LAMBDA_BUNDLE, agents.AGENT_BUNDLE, frontend.SITE
    for name in ("lambda", "agents", "site"):
        (root / name).mkdir()
        (root / name / "placeholder.txt").write_text(name)
    backend.LAMBDA_BUNDLE, agents.AGENT_BUNDLE, frontend.SITE = (
        root / "lambda",
        root / "agents",
        root / "site",
    )
    yield
    backend.LAMBDA_BUNDLE, agents.AGENT_BUNDLE, frontend.SITE = saved


def build(region: str = "us-east-1", **context: str) -> list[cdk.Stack]:
    app = cdk.App(context={**CONTEXT, "adminEmail": "admin@example.com", **context})
    return Application(app, ENV.account, region).build()


def synth(**context: str) -> Synth:
    (stack,) = build(**context)
    stack.node.root.synth()  # type: ignore[attr-defined]
    return Synth(Template.from_stack(stack), Annotations.from_stack(stack))


@pytest.fixture(scope="session")
def default(bundles: None) -> Synth:
    return synth()


@pytest.fixture(scope="session")
def other_region(bundles: None) -> Synth:
    """The main stack outside us-east-1, where the edge stack holds the web ACL (and CloudFront and
    WAF publish their metrics in a Region this stack cannot alarm on)."""
    _edge, main = build(
        "eu-west-1",
        webAclArn="arn:aws:wafv2:us-east-1:111122223333:global/webacl/air/0",
    )
    main.node.root.synth()  # type: ignore[attr-defined]
    return Synth(Template.from_stack(main), Annotations.from_stack(main))


@pytest.fixture(scope="session")
def disposable(bundles: None) -> Synth:
    """A test deployment: data deleted with the stack, alarms mailed, no web search."""
    return synth(
        prefix="mr-test",
        alarmEmail="ops@example.com",
        retainData="false",
        webSearch="false",
        logRetentionDays="30",
    )
