"""Per-model configuration packages.

Each subfolder here (e.g. ``medgemma_27b``) contains everything model-specific:
the :class:`ModelSpec`, the list of :class:`DeploymentPlan` / :class:`ExperimentConfig`
experiments, and any prompts or reference inputs used by supporting scripts.

The generic deployment infrastructure lives in ``vllm_ec2_bench``.
"""
