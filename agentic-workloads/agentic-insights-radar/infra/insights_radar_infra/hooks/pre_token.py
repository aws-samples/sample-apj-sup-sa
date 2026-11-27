# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Cognito pre-token-generation hook for federated sign-in (``authMode=federated``).

The provider decides who may sign in, so everyone it lets in is a viewer; the listed administrators are
made members of the admin group at sign-in. Membership, not just the token claim: the API confirms every
request against the user's live groups and would lower a claim-only admin to viewer. Group membership set
in the app (editor, or admin for someone not on the list) is kept: the hook only adds. It runs at every
sign-in and refresh, so an address added to the list takes effect with the next token; taking an address
off the list stops re-adding it, and an administrator then removes the membership.

Deployed as its own small asset (this folder), so it imports nothing from the rest of the stack.
"""

from __future__ import annotations

import json
import os
from typing import Any

_cognito: Any = None


def _client() -> Any:
    global _cognito
    if _cognito is None:
        import boto3  # type: ignore[import-not-found]  # the Lambda runtime provides it

        _cognito = boto3.client("cognito-idp")
    return _cognito


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    admins = frozenset(json.loads(os.environ.get("ADMIN_EMAILS", "[]")))
    request = event.get("request", {})
    attrs = request.get("userAttributes", {})
    email = str(attrs.get("email", "")).strip().lower()
    # Only an address the provider says it verified, unless the operator trusts every address the provider
    # issues (TRUST_PROVIDER_EMAIL, for a company directory that assigns them; some send no
    # email_verified). A provider that lets people type any address would otherwise let someone claim an
    # administrator's.
    verified = (
        os.environ.get("TRUST_PROVIDER_EMAIL") == "true"
        or str(attrs.get("email_verified", "")).lower() == "true"
    )
    groups = list(request.get("groupConfiguration", {}).get("groupsToOverride", []))
    if email and verified and email in admins and "admin" not in groups:
        _client().admin_add_user_to_group(
            UserPoolId=event["userPoolId"],
            Username=event["userName"],
            GroupName="admin",
        )
        groups.append("admin")
        event["response"] = {
            "claimsOverrideDetails": {
                "groupOverrideDetails": {"groupsToOverride": groups}
            }
        }
    return event
