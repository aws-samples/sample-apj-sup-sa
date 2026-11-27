# Contributing Guidelines

Thank you for your interest in contributing to our project. Whether it's a bug report, new feature,
correction, or additional documentation, we greatly value feedback and contributions from our community.

Please read through this document before submitting any issues or pull requests to ensure we have all
the necessary information to effectively respond to your bug report or contribution.

## Reporting Bugs/Feature Requests

We welcome you to use the GitHub issue tracker to report bugs or suggest features.

When filing an issue, please check existing open, or recently closed, issues to make sure somebody else
hasn't already reported the issue. Please try to include as much information as you can. Details like
these are incredibly useful:

* A reproducible test case or series of steps
* The version of our code being used
* Any modifications you've made relevant to the bug
* Anything unusual about your environment or deployment

## Contributing via Pull Requests

Contributions via pull requests are much appreciated. Before sending us a pull request, please ensure that:

1. You are working against the latest source on the *main* branch.
2. You check existing open, and recently merged, pull requests to make sure someone else hasn't addressed
   the problem already.
3. You open an issue to discuss any significant work - we would hate for your time to be wasted.

To send us a pull request, please:

1. Fork the repository.
2. Modify the source; please focus on the specific change you are contributing. If you also reformat all
   the code, it will be hard for us to focus on your change.
3. Run the same gates CI runs (`.github/workflows/ci.yml`; in `aws-samples/sample-apj-sup-sa` run them locally, since GitHub runs workflows only from a repository's root). All of them must pass; the coverage one is the one that surprises
   people most, so start there.

   | Gate | Command |
   |---|---|
   | Tests, with **100% line and branch coverage** | `cd <agents\|infra> && uv run pytest -q` (their `pyproject.toml` turns coverage on and sets `fail_under = 100`); `cd backend && uv run pytest -q --cov --cov-branch` (without `--cov` the backend measures nothing and its `fail_under` never applies) |
   | Lint | `cd <project> && uv run ruff check .` |
   | Types | `cd <project> && uv run mypy --strict src` (infra: `uv run mypy --strict insights_radar_infra tests app.py`) |
   | Web app | `cd frontend && npm ci --ignore-scripts && npm run lint && npm test && npm run build` (no install scripts, as `build.py` and `deploy.sh` install: machines that build often hold AWS credentials) |
   | MCP server | `cd mcp && npm ci --ignore-scripts && npm run lint && npm run typecheck && npm test && npm run build` (`npm test` enforces 100% line, branch, function and statement coverage) |
   | Shell scripts | `uvx --from shellcheck-py==0.10.0.1 shellcheck -x deployment/*.sh` |
   | The live suite, linted and collected (it is not run) | `cd e2e && uvx ruff@0.16.9 check . && uv run --frozen --with mypy==2.3.1 mypy --strict --ignore-missing-imports --explicit-package-bases . && uv run --frozen pytest --collect-only -q` |

   The infra suite also runs the deployment scripts against stand-in AWS commands and checks that
   the customer documents quote the code's suppressions, alarm counts and limits word for word, so a
   change to a limit needs the matching documentation change in the same pull request. The live
   suite in `e2e/` needs a test deployment (see the implementation guide, End-to-end tests).
   `.github/workflows/ci.yml` is the full list; it also runs bandit, pip-audit, gitleaks and
   checkov.
4. Commit to your fork using clear commit messages.
5. Send us a pull request, answering any default questions in the pull request interface.
6. Pay attention to any automated CI failures reported in the pull request, and stay involved in the
   conversation.

GitHub provides additional documentation on [forking a repository](https://help.github.com/articles/fork-a-repo/)
and [creating a pull request](https://help.github.com/articles/creating-a-pull-request/).

## Finding contributions to work on

Looking at the existing issues is a great way to find something to contribute on. As our projects, by
default, use the default GitHub issue labels (enhancement/bug/duplicate/help wanted/invalid/question/wontfix),
looking at any 'help wanted' issues is a great place to start.

## Code of Conduct

This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct).
For more information see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq) or contact
opensource-codeofconduct@amazon.com with any additional questions or comments.

## Security issue notifications

If you discover a potential security issue in this project we ask that you notify AWS/Amazon Security via
our [vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/). Please do
**not** create a public GitHub issue.

## Licensing

See the repository's [LICENSE](../../LICENSE) file (MIT-0) for this sample's licensing. We will ask you to confirm the licensing of
your contribution.
