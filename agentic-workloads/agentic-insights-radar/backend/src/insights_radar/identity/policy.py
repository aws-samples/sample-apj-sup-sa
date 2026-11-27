# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Role -> action authorisation table. One place decides; every handler asks it."""

from __future__ import annotations

import json
import logging
from enum import StrEnum

from insights_radar.core.errors import ForbiddenError
from insights_radar.identity.principal import Principal, PrincipalKind, Role

log = logging.getLogger(__name__)


class Action(StrEnum):
    READ = "read"
    CHAT = "chat"
    GIVE_FEEDBACK = "feedback.give"
    MARK_WORKING = "working.mark"
    DRAFT_CONTENT = "content.draft"
    RADAR_CREATE = "radar.create"
    RADAR_EDIT = "radar.edit"
    SUGGESTION_REVIEW = "suggestion.review"
    PRODUCT_MANAGE = "product.manage"
    # Raw passages and the document list of a product's private documents. Editors and up: a viewer,
    # the default role for every new user, could pull 20 raw passages of the internal roadmap per
    # query while the module fields that name a product were hidden from them (SR2-12).
    PRODUCT_READ = "product.read"
    MODULE_MANAGE = "module.manage"
    MODULE_RUN = "module.run"
    BUDGET_MANAGE = "budget.manage"
    ANDON_MANAGE = "andon.manage"
    USER_MANAGE = "user.manage"
    AUDIT_READ = "audit.read"
    HEALTH_READ = "health.read"
    GRAPH_EDIT = "graph.edit"
    SAMPLE_LOAD = "sample.load"
    PROFILE_MANAGE = "profile.manage"


_VIEWER = frozenset({Action.READ, Action.CHAT})
_EDITOR = _VIEWER | {
    Action.GIVE_FEEDBACK,
    Action.MARK_WORKING,
    Action.DRAFT_CONTENT,
    Action.RADAR_CREATE,
    Action.RADAR_EDIT,
    Action.SUGGESTION_REVIEW,
    Action.PRODUCT_READ,
}
_ADMIN = frozenset(Action)

POLICY: dict[Role, frozenset[Action]] = {Role.VIEWER: _VIEWER, Role.EDITOR: _EDITOR, Role.ADMIN: _ADMIN}


class Policy:
    @staticmethod
    def allows(principal: Principal, action: Action) -> bool:
        if principal.kind is not PrincipalKind.USER:
            return False  # module runs act only through the agent tool gate
        return action in POLICY[principal.role]

    @classmethod
    def require(cls, principal: Principal, action: Action) -> None:
        if not cls.allows(principal, action):
            # Probing for a privilege leaves a trail: who asked for what.
            log.warning(
                json.dumps(
                    {
                        "event": "authz.denied",
                        "actor": principal.actor,
                        "role": principal.role.value,
                        "action": action.value,
                    }
                )
            )
            raise ForbiddenError(f"your role does not allow {action.value}", {"action": action.value})
