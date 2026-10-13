# `dev/` — developer-only tooling (NOT shipped)

Everything under `dev/` is for maintainers of this sample. It is **excluded from
both deploy paths**: the demo packager (`infrastructure/scripts/package_and_upload.sh`)
never bundles it, and the workshop packager (`infrastructure/scripts/package_for_workshop.sh`)
excludes `*dev/*` from the participant zip. None of it runs at deploy time or at
runtime — the live system is built entirely from CloudFormation + the curated
`agent_code.zip`.

## `dev/evaluation/` — Strands Evals harness (was `app/agentcore_strands/evaluation/`)

Offline evaluation of the deployed agent. Moved here because it is authoring/QA
tooling, not part of the agent runtime package.

- `generate_ground_truth.py` — query a local PostgreSQL `timely_unicorn` DB with
  explicit `account_id` filters to produce `dataset/validation/ground_truth.json`.
- `build_experiment.py` — expand the ground truth into a Strands Evals experiment
  (`dataset/validation/experiment.json`), ~100 cases across SQL/SOP/guardrail/RLS/RBAC.
- `run_evaluation.py` — run the experiment against the deployed AgentCore Runtime
  via `agentcore invoke`, authenticating per-persona through Cognito, and score
  with an LLM-as-judge. (This is the Step-10 "evaluation" exercise's reference
  implementation; the workshop page itself drives the `agentcore eval` CLI.)

Paths inside these scripts are relative to this folder: `../../dataset/validation/...`
and `../../app/agentcore_strands/gateway_config.json`. Run them from anywhere
(`python3 dev/evaluation/run_evaluation.py`) — they resolve paths from `__file__`.

## `dev/specs/` — project specification

`dev/specs/agentic-analytics/` holds the spec the sample was built from:
`requirements.md`, `design.md`, and `tasks.md`. Reference/authoring material only —
nothing here is consumed at build or runtime.

## `dev/skills/` — procedural skills for maintainers

Step-by-step playbooks for recurring maintenance tasks:

- `workshop-deployment/SKILL.md` — package, sync assets to S3, and push to Workshop Studio.
- `overlay-management/SKILL.md` — keep `workshop/code/` TODO overlays in sync with `app/`.
