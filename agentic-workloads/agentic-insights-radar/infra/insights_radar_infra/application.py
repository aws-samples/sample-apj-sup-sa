# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The stacks for one deployment, as ``app.py`` and the tests build them."""

from __future__ import annotations

import re

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from insights_radar_infra.config import DeployConfig
from insights_radar_infra.edge import EDGE_REGION, EdgeStack
from insights_radar_infra.nag import apply_suppressions
from insights_radar_infra.scanners import ScannerSkips
from insights_radar_infra.stack import AgenticInsightsRadarStack

_ACL_ARN = re.compile(
    r"^arn:aws[a-z-]*:wafv2:us-east-1:\d{12}:global/webacl/[\w-]+/[\w-]+$"
)


class Application:
    """Outside us-east-1 a deployment has two phases: the edge stack, then the main stack with
    ``-c webAclArn=<the edge stack's WebAclArn output>``. ``deployment/deploy.sh`` runs both."""

    def __init__(self, app: cdk.App, account: str | None, region: str | None) -> None:
        self._app = app
        self._account = account
        self._region = region
        self.config = DeployConfig.from_context(app.node.try_get_context)
        self.name = (
            "AgenticInsightsRadar"
            if self.config.prefix == "air"
            else f"AgenticInsightsRadar-{self.config.prefix}"
        )

    def build(self) -> list[cdk.Stack]:
        stacks: list[cdk.Stack] = []
        acl: str | None = None
        if self._region != EDGE_REGION:
            stacks.append(
                EdgeStack(
                    self._app,
                    f"{self.name}-{self._region}-Edge",
                    self.config,
                    self._region or "",
                    env=cdk.Environment(account=self._account, region=EDGE_REGION),
                )
            )
            acl = self._app.node.try_get_context("webAclArn")
            if not acl:
                return self._checked(stacks)  # first phase
            if not _ACL_ARN.fullmatch(acl):
                raise ValueError(
                    "webAclArn must be the edge stack's WebAclArn output (a us-east-1 web ACL)"
                )
        main = AgenticInsightsRadarStack(
            self._app,
            self.name,
            self.config,
            web_acl_arn=acl,
            env=cdk.Environment(account=self._account, region=self._region),
        )
        for edge in stacks:
            main.add_stack_dependency(
                edge
            )  # destroy removes the distribution before its web ACL
        apply_suppressions(main)
        return self._checked([*stacks, main])

    def _checked(self, stacks: list[cdk.Stack]) -> list[cdk.Stack]:
        for stack in stacks:
            ScannerSkips(stack).apply()
        cdk.Aspects.of(self._app).add(AwsSolutionsChecks(verbose=True))
        return stacks
