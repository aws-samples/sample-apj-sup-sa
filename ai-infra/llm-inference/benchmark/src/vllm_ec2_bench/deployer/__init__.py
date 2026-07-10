"""Deployment infrastructure: ResourceManager, UserDataRenderer, DeploymentState, Runner.

Split into submodules:

* :mod:`.resources` — IAM / SG / subnet / AMI plumbing.
* :mod:`.user_data` — Jinja2 rendering of cloud-init user-data.
* :mod:`.state` — Pydantic record of live deployment state.
* :mod:`.capacity` — strategy pattern (spot / on-demand / ODCR / capacity-block).
* :mod:`.runner` — :class:`DeploymentRunner` orchestrator.
* :mod:`.secrets` — ``upsert_hf_token`` helper for AWS Secrets Manager.
"""
from .resources import ResourceManager
from .runner import DeploymentRunner
from .secrets import upsert_hf_token
from .state import DeploymentState
from .user_data import UserDataRenderer

__all__ = [
    "DeploymentRunner",
    "DeploymentState",
    "ResourceManager",
    "UserDataRenderer",
    "upsert_hf_token",
]
