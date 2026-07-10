# Quickstart: Generative Mode

## Local (empty DB) — reproduce the bug, then the fix

```bash
cd backend
. .venv/bin/activate
# Start the local pgvector container (port 55432) and migrate, but DO NOT load the bank:
#   (docker compose up the db, then `python -m src.db_migrate` against it)
# Run the focused tests:
pytest tests/test_generative_mode.py -q
```

Expected after implementation:
- empty-bank Easy/3-min prep returns a plan (no `RuntimeError`), `domain_coverage_reduced=True`;
- bank-present + flag-off prep is unchanged (no generation call);
- generation-returns-nothing path raises the honest error.

## Operator force (any instance)

```bash
# Force generative mode even when a bank exists (testing/demo):
export GENERATIVE_MODE=1
# restart the backend; new sessions will be generated and flagged.
```

## Deployed stack (us-west-2) — verify the live path that currently fails

The deployed backend currently 503s on Easy/3-min when the bank is empty. After deploying the new
backend image:

1. Rebuild + push the backend image (CodeBuild path) and force a new backend deployment.
2. In a browser at the CloudFront demo URL: sign in → complete the wizard → pick **Easy + 3 min** →
   **Start interview**. It MUST advance to the mic-check screen (no "no approved archetypes" error).
3. (Optional) confirm in the backend logs the per-session line noting the Principle VII relaxation,
   and that the new `question_archetype` rows have `source='generated'`.

No bank-load step is required for the interview to start — that is the success criterion (SC-001).
The bank loader (`bank/load_fixture.py`) remains available and, when run, restores bank-served
behavior (higher-quality, human-vetted questions) per Principle VII d.
