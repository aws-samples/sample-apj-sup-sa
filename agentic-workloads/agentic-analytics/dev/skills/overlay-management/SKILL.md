---
name: overlay-management
description: Manage workshop code overlays — TODO versions of source files for participant exercises. Use when editing app/ Lambda or agent files to ensure workshop overlays stay in sync.
metadata:
  author: agentic-analytics-team
  version: "1.0"
---

# Workshop Code Overlay Management

## What Overlays Are

`workshop/code/` contains modified versions of `app/` files with TODO placeholders. During packaging, these override the working code in the repo ZIP deployed to EC2 Code Editors.

- `app/` = working code (complete, functional)
- `workshop/code/` = overlay with TODOs (commented-out code, `None` placeholders)

## Current Overlays

```
workshop/code/
├── exercises/
│   └── basic_agent.py              # TODOs 1.2-1.4
└── app/agentcore_strands/
    ├── unicorn_rental_agent.py      # TODOs 2.3.1, 2.3.2, 2.4, 2.8
    ├── guardrails/
    │   └── deploy_guardrail.py      # TODO 8.1
    └── policy/
        └── deploy_policy.py         # TODO 7.1
```

## Rules

### When editing `app/` files:
1. Check if `workshop/code/` has a corresponding overlay
2. If yes, apply the same change BUT preserve TODO patterns
3. If the overlay has no TODOs left, DELETE it
4. Always verify overlays parse: `python3 -c "import ast; ast.parse(open('file').read())"`

### When adding TODOs:
1. Create overlay mirroring the `app/` path
2. Comment out or replace key code with `# TODO X.Y (Step X): description`
3. Add instructions to the workshop content markdown
4. Number as `TODO {step}.{sequence}`

### When removing TODOs:
1. Uncomment the code in the overlay
2. If no TODOs remain, delete the overlay file
3. Update workshop content from "uncomment TODO" to "examine the code"

## Deleted Overlays (no TODOs, code works from start)

- `tools/prebaked_sql_toolset_lambda.py` — RLS active from start
- `tools/api_integration_toolset_lambda.py` — same
- `tools/custom_sql_toolset_lambda.py` — same
- `infra/interceptor_lambda.py` — header propagation active from start

## Common Pitfalls

- **Forgetting to sync**: Editing `app/` without updating overlay → overlay overwrites your changes in the packaged ZIP
- **Stale overlays**: Refactoring code without updating overlay → broken TODO file
- **Ordering bugs**: Overlay once had `BedrockModel(**kwargs)` before `kwargs` was defined — always verify syntax
