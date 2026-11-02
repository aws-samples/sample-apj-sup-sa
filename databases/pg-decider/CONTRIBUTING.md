# Contributing

Issues and merge requests are welcome.

## Before you open a merge request

1. Run the regression suite. It needs Docker, but no local PostgreSQL and no model:
   ```bash
   make docker-test                # PostgreSQL 16
   make docker-test PG_MAJOR=14    # CI runs 14, 15, 16 and 17
   ```
2. Any change in behaviour needs a test in `test/sql/` with matching output in `test/expected/`. Write tests against the rules of `test/mock_decider.py`, which its docstring lists, and extend the mock if a new code path needs it. When output changes on purpose, copy `test/results/<name>.out` over `test/expected/<name>.out` and review the diff.
3. If you change how questions are phrased or sent to the model, also run `test/live/live_test.sql` against a real server and put the before/after numbers in the merge request (see README, "Development").

## Layout

- `sql/decider--<version>.sql`: the whole extension. `_decider()` is the PL/Python engine; the public functions are SQL wrappers that build the question JSON.
- `sql/decider--<old>--<new>.sql`: upgrade scripts. Every release after 0.1.0 needs one.
- `decider.control`: metadata and `default_version`.
- `test/`: pg_regress tests, the mock server and the Docker runner.

## Rules

- Threads must never call `plpy`. Only the main thread does SPI, notices and errors.
- Keep the SQL API stable. Adding functions is fine. Changing a signature needs a new major version and an upgrade script.
- Keep defaults in sync across the SQL file header, `load_cfg()` and the README.

## Releasing

Bump `default_version` in `decider.control`. Then add `sql/decider--X.Y.Z.sql` and `sql/decider--OLD--X.Y.Z.sql`, update `decider_version()`, add a `CHANGELOG.md` entry, and tag `vX.Y.Z`. The CI `version-consistency` job checks that these agree.
