# sagemaker-marketplace-onboarding

An [Agent Skill](https://docs.claude.com/en/docs/claude-code/skills) that walks a model provider
through making their container compatible with the AWS SageMaker Marketplace container contract —
`/ping` + `/invocations` (Real-Time, Streaming, Async, Batch Transform), optional bidirectional
WebSocket, optional per-inference metering, weights packaging, local testing, ECR push, and an
optional `CreateModelPackage` hand-off for sellers who also want to publish.

## What it does

An 11-phase interactive walkthrough, one phase at a time with confirmation before advancing:

| Phase | What happens |
|-------|--------------|
| 0 | Goal (container-only vs. also-list) + project state (existing vs. greenfield) |
| 1 | Inventory an existing project (framework, HTTP routes, weights) — read-only |
| 2 | Discovery interview (invocation modes, modality, billing model) via structured questions |
| 3 | Gap analysis vs. the container contract — report only, never edits the original project |
| 4 | Scaffold a sibling directory from this skill's templates |
| 5 | Walk through `/ping`, `/invocations`, streaming, `/execution-parameters`, WebSocket |
| 6 | Dockerfile contract — exec-form ENTRYPOINT, no baked weights, no NVIDIA drivers, no `tini` |
| 7 | Model weights packaging (`model.tar`, uncompressed, `.safetensors` preferred) |
| 8 | Local testing gate — must pass before ECR push |
| 9 | ECR push + vulnerability scan |
| 10 | Pre-submission checklist |
| 11 | `CreateModelPackage` hand-off (only if the seller also wants to publish) |
| 12 | *(optional, ask-only)* Operating a live listing — observability + IAM temporary delegation pointers |

Never edits a provider's existing project files — it always scaffolds alongside into a sibling
directory (e.g. `<project>/sagemaker/`).

## What's in this skill

```
sagemaker-marketplace-onboarding/
├── SKILL.md                              ← Skill instructions (the agent reads this)
├── README.md                             ← This file
├── reference/
│   ├── contract.md                       ← One-page endpoint/header/timing cheat sheet
│   ├── timing.md                         ← Every hard timing constraint
│   ├── checklist.md                      ← Full pre-submission checklist template
│   ├── gap-checks.md                     ← Gap-analysis checklist for Phase 3
│   ├── websocket.md                      ← Bidirectional streaming protocol, Ping/Pong, metadata channel
│   ├── billing.md                        ← Hourly vs. per-inference, dimensions, freeze rule
│   ├── logging.md                        ← CloudWatch log groups, structured JSON logging
│   ├── marketplace-listing.md            ← CreateModelPackage API skeleton + validation job
│   ├── observability.md                  ← CloudWatch metrics catalog + EMF business-metric emission
│   └── iam-temporary-delegation.md       ← Buyer-approved temporary support access for a live listing
└── templates/
    ├── Dockerfile, app.py, model_loader.py, inference.py, metering.py, websocket_handler.py
    ├── package_model.sh, requirements.txt, supervisord.conf, PRE_SUBMISSION_CHECKLIST.md
    └── test/  (test_input.json, test_local.sh, test_streaming.py, test_websocket.py)
```

## Requirements

- No build step — this skill is Markdown instructions plus copy-and-fill Python/shell templates.
- The agent needs Write/Read/shell access to scaffold the sibling directory and run local Docker
  tests (Phase 8) — no npm/Node dependency, unlike some other skills in this repo.
- Docker (for Phase 8 local testing) and the AWS CLI + an AWS account (for Phase 9 ECR push and
  Phase 11 `CreateModelPackage`) are the provider's own prerequisites, not the skill's.

---

## Installation

### Claude Code

**Option A — Plugin marketplace (recommended).** This repo ships a Claude Code plugin
marketplace, so you can install the skill (and get automatic updates) with two commands inside
Claude Code:

```
/plugin marketplace add aws-samples/sample-apj-sup-sa
/plugin install sagemaker-marketplace-onboarding@apj-sup-sa
```

See [`../../README.md`](../../README.md) for marketplace details.

**Option B — Copy into your skills directory.** Copy this folder into either scope:

```bash
# Personal (all projects)
cp -R ai-infra/sagemaker-marketplace-onboarding ~/.claude/skills/sagemaker-marketplace-onboarding

# Project-scoped (checked in for your team)
mkdir -p .claude/skills
cp -R ai-infra/sagemaker-marketplace-onboarding .claude/skills/sagemaker-marketplace-onboarding
```

Restart Claude Code (or run `/doctor`) and confirm the skill is listed. It activates automatically
when you ask to onboard a model onto SageMaker Marketplace.

### Codex

Codex CLI discovers Agent Skills from a `skills/` directory. Place this folder where your Codex
setup looks for skills:

```bash
# Personal scope
mkdir -p ~/.codex/skills
cp -R ai-infra/sagemaker-marketplace-onboarding ~/.codex/skills/sagemaker-marketplace-onboarding

# Or project scope, checked into your repo
mkdir -p .codex/skills
cp -R ai-infra/sagemaker-marketplace-onboarding .codex/skills/sagemaker-marketplace-onboarding
```

The `SKILL.md` frontmatter (`name` + `description`) is what Codex matches on to decide when to
load the skill. Confirm your Codex version's skills path in its docs — some builds read
`AGENTS.md`/skill references from the working directory instead.

### Kiro

Kiro loads skill-style guidance from its steering/rules configuration. Vendor the skill into your
Kiro workspace and point steering at it:

```bash
mkdir -p .kiro/skills
cp -R ai-infra/sagemaker-marketplace-onboarding .kiro/skills/sagemaker-marketplace-onboarding
```

Then add a steering rule (e.g. in `.kiro/steering/`) that tells Kiro to read
`.kiro/skills/sagemaker-marketplace-onboarding/SKILL.md` when the user wants to onboard a model
container onto SageMaker Marketplace, and to follow its phase-by-phase workflow. Because the skill
is plain Markdown plus templates, no Kiro-specific packaging is required.

---

## Usage

Once installed, ask for it in plain language:

```
Help me onboard my model to SageMaker Marketplace.
```

or:

```
Review my container against the SageMaker Marketplace spec.
```

Trigger phrases the skill recognizes include: "onboard my model to SageMaker Marketplace", "list
my model on SageMaker Marketplace", "help me build a SageMaker Marketplace container", "package my
model for SageMaker Marketplace" — or attaching the SageMaker Marketplace Model Listing Guide.

### What to expect

1. **Two up-front questions** — container-only vs. also-list, and existing project vs. greenfield.
2. **If you have an existing project**, it inventories your code (read-only) and reports back
   framework, HTTP routes, and where weights live for you to confirm or correct.
3. **A structured discovery interview** — invocation modes, modality, framework, billing model —
   using multiple-choice questions, not free text, because the answers change what gets generated.
4. **A gap-analysis punch list** (existing projects only) before anything is written.
5. **A scaffolded sibling directory** — never your existing files — with a Dockerfile, server,
   model loader, inference stub, tests, and a pre-submission checklist tailored to your answers.
6. **A local-testing gate** that must pass (container start, `/ping`, `/invocations`, network
   isolation, SIGTERM handling) before it lets you push to ECR.
7. **Optionally**, help running `CreateModelPackage` with a validation job if you're also listing.

Everything outside the container contract — FDP enrollment, IAM roles, pricing, EULA, regions, the
customer-facing notebook, and publishing — is manual work you handle via your AWS account team and
the Marketplace Management Portal; the skill points you at the right docs rather than walking you
through those steps.
