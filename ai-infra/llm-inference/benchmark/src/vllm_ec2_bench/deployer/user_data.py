"""UserDataRenderer — turn an ExperimentConfig + Catalog into cloud-init user-data.

Uses an internal :class:`Renderer` (see :mod:`._jinja_renderer`) which wraps
Jinja2 with ``StrictUndefined`` so any missing variable raises at render
time (not at EC2 boot time when debugging is painful).

Hardware facts (family, num_accelerators) come from the Catalog passed at
render time — the DeploymentPlan carries only the instance type string.

Security
--------
The HuggingFace token is NEVER template-interpolated into the rendered
script. Instead, the script fetches the token value at boot from AWS
Secrets Manager using the instance role
(``secretsmanager:GetSecretValue`` granted by
ResourceManager._ensure_instance_profile). Only the *name* of the
secret is passed as a template variable (``hf_secret_name``), never the
value.
"""
from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any

from ._jinja_renderer import make_renderer
from ..data import ExperimentConfig

if TYPE_CHECKING:
    from ..data.catalog import Catalog

LOG = logging.getLogger(__name__)


class UserDataRenderer:
    """Render an ``ExperimentConfig`` into a cloud-init ``user-data`` string."""

    def __init__(self) -> None:
        # See ._jinja_renderer for the security rationale of the underlying
        # template engine choices (StrictUndefined, autoescape=False because
        # the output is bash, etc).
        self._renderer = make_renderer(
            package="vllm_ec2_bench",
            templates_dir="templates",
        )

    # ------------------------------------------------------------------
    def render(
        self,
        config: ExperimentConfig,
        catalog: "Catalog",
        *,
        hf_secret_name: str | None,
        vllm_api_key: str,
    ) -> str:
        """Render the cloud-init user-data as plain text.

        Parameters
        ----------
        hf_secret_name
            Name of the AWS Secrets Manager secret holding the HF token.
            If None, the rendered script doesn't attempt to fetch a token
            (fine for ungated models).
        vllm_api_key
            API key the instance exposes on port 8000 (random per-launch).
        """
        facts = catalog.hardware(config.deployment.instance_type)
        ctx = self._build_context(
            config, facts,
            hf_secret_name=hf_secret_name, vllm_api_key=vllm_api_key,
        )
        template_name = "user_data_neuron.sh.j2" if facts.family == "neuron" else "user_data_gpu.sh.j2"
        return self._renderer.render(template_name, ctx)

    def render_b64(
        self,
        config: ExperimentConfig,
        catalog: "Catalog",
        *,
        hf_secret_name: str | None,
        vllm_api_key: str,
    ) -> str:
        """Render and base64-encode (suitable for ``UserData`` field)."""
        text = self.render(
            config, catalog,
            hf_secret_name=hf_secret_name, vllm_api_key=vllm_api_key,
        )
        return base64.b64encode(text.encode()).decode()

    # ------------------------------------------------------------------
    @staticmethod
    def _build_context(
        config: ExperimentConfig,
        facts: Any,            # HardwareFacts — avoid importing to prevent cycles
        *,
        hf_secret_name: str | None,
        vllm_api_key: str,
    ) -> dict[str, Any]:
        ms = config.model_spec
        dp = config.deployment

        log_tag = f"{ms.resource_prefix}-{dp.experiment_id}"
        ctx: dict[str, Any] = {
            "log_tag": log_tag,
            "model_id": ms.hf_model_id,
            "served_model_name": ms.served_model_name,
            "hf_secret_name": hf_secret_name,
            "region": dp.region,  # always passed so templates can curl Secrets Manager
            "vllm_api_key": vllm_api_key,
            "tensor_parallel_size": dp.tensor_parallel,
            "data_parallel_size": dp.data_parallel,
            "pipeline_parallel_size": dp.pipeline_parallel,
            "max_model_len": config.effective_max_model_len,
            "gpu_memory_utilization": f"{config.gpu_memory_utilization:.2f}",
            "enable_prefix_caching": config.enable_prefix_caching,
            "extra_serve_flags": dp.extra_serve_flags,
            # Plan-author-provided per-model env vars rendered as
            # ``-e KEY=VALUE`` Docker flags. The validator on DeploymentPlan
            # guarantees keys match ``[A-Z_][A-Z0-9_]*`` so render-time
            # quoting is safe.
            "extra_env_vars": dict(dp.extra_env_vars),
            "dtype": ms.dtype,
            "container_name": ms.container_name,
            "ready_check_name": f"{ms.resource_prefix}-ready-check",
        }

        if facts.family == "gpu":
            ctx["vllm_image"] = ms.vllm_gpu_image
            if dp.mig_profile is not None:
                assert dp.mig_profile_ids is not None, "validator guarantees this"
                ctx["mig_profile"] = dp.mig_profile
                ctx["mig_profile_id"] = dp.mig_profile_ids[0]
                ctx["mig_replicas_per_gpu"] = dp.mig_replicas_per_gpu
                ctx["num_gpus"] = facts.num_accelerators
            else:
                ctx["mig_profile"] = None
                ctx["mig_profile_id"] = 0
                ctx["mig_replicas_per_gpu"] = 1
                ctx["num_gpus"] = facts.num_accelerators
        else:  # neuron
            ctx["neuron_image"] = ms.neuron_image_template.format(region=dp.region)
            ctx["neuron_visible_devices"] = "ALL"
            ctx["num_accelerators"] = facts.num_accelerators

        return ctx


__all__ = ["UserDataRenderer"]
