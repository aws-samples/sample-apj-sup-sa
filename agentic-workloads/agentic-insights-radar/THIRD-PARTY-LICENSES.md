# Third-party licenses

This sample depends on the open source packages below. They are not redistributed in this
repository: the build downloads them from PyPI and npm. The table lists the **direct runtime
dependencies** of each project (development and test tools are not listed). Versions are the exact
pins in each project's manifest; licenses were read from the installed package metadata
(`License-Expression` or `License` for Python, `license` in `package.json` for npm).

Each package's own license text is the authoritative statement of its terms. Transitive
dependencies carry their own licenses, which you can list with `uv pip list` / `uv tree` in a
Python project and `npm ls --all` in `frontend/` and `mcp/`.

## Python

| Package | Version | License | Used by |
|---|---|---|---|
| boto3 | 1.43.102 | Apache-2.0 | `backend`, `agents` |
| httpcore | 1.0.9 | BSD-3-Clause | `backend` |
| bedrock-agentcore | 1.23.1 | Apache-2.0 | `agents` |
| strands-agents | 1.57.0 | Apache-2.0 | `agents` |
| strands-agents-tools | 0.8.9 | Apache-2.0 | `agents` |
| playwright | 1.63.0 | Apache-2.0 | `agents` |
| nest-asyncio | 1.6.0 | BSD (BSD-2-Clause per the project's LICENSE file; the package metadata says "BSD") | `agents` |
| httpx | 0.28.1 | BSD-3-Clause | `agents` |
| mcp | 2.2.0 | MIT | `agents` |
| httpx2 | 2.13.1 | BSD-3-Clause | `agents` |
| httpcore2 | 2.13.1 | BSD-3-Clause | `agents` |
| anyio | 4.15.1 | MIT | `agents` |
| aws-cdk-lib | 2.271.0 | Apache-2.0 | `infra` |
| constructs | 10.8.1 | Apache-2.0 | `infra` |
| cdk-nag | 2.38.2 | Apache-2.0 | `infra` |

`agents` also depends on `insights-radar-backend`, which is the `backend` project in this repository
(MIT-0, see [LICENSE](../../LICENSE)).

## MPL-2.0 packages (used unmodified)

These packages are licensed under the Mozilla Public License 2.0. This sample uses them unmodified
and does not include or redistribute any of their files: the build downloads them at the versions
below. The full text of the licence is at https://www.mozilla.org/en-US/MPL/2.0/, and each
package's source code is available at the link given.

| Package | Version | Copyright holder | License | Source code | Used by |
|---|---|---|---|---|---|
| certifi | 2026.10.23 | Kenneth Reitz and the certifi contributors (the CA bundle is derived from Mozilla's `certdata.txt`) | [MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/) | https://github.com/certifi/python-certifi/tree/2026.10.23 (also https://pypi.org/project/certifi/2026.10.23/) | `backend`, `agents`, `e2e` (transitive, through `httpcore`, `httpx` and `requests`) |
| pathspec | 1.1.1 | Copyright © 2013-2026 Caleb P. Burns | [MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/) | https://github.com/cpburnz/python-pathspec/tree/v1.1.1 (also https://pypi.org/project/pathspec/1.1.1/) | `backend`, `agents`, `infra` (development only, through `mypy`) |
| lightningcss | 1.33.0 | Devon Govett and the Lightning CSS contributors | [MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/) | https://github.com/parcel-bundler/lightningcss/tree/v1.33.0 (also https://www.npmjs.com/package/lightningcss/v/1.33.0) | `frontend`, build time only (CSS processing during `npm run build`; not part of the built web app) |

## Attribution: caniuse-lite (CC-BY-4.0)

The frontend build uses [caniuse-lite](https://github.com/browserslist/caniuse-lite) 1.0.30001814,
the browser support data © Alexis Deveria / caniuse.com contributors, licensed under
[Creative Commons Attribution 4.0 International (CC-BY-4.0)](https://creativecommons.org/licenses/by/4.0/).
It is build-time data only (browser targets for the CSS and JavaScript build), used unmodified, and
is not part of the built web app.

## Content included in this repository

| Work | Version | License | Where |
|---|---|---|---|
| [humanizer](https://github.com/blader/humanizer) skill, Copyright (c) 2025 Siqi Chen | v3.1.0, with local changes listed in its `NOTICE` | MIT | `backend/src/insights_radar/modules/default_skills/humanizer/` (its `LICENSE` and `NOTICE` are next to it) |

## npm (web app, `frontend/package.json` `dependencies`)

| Package | Version | License |
|---|---|---|
| @cloudscape-design/components | 3.0.1391 | Apache-2.0 |
| @cloudscape-design/global-styles | 1.0.71 | Apache-2.0 |
| d3 | 7.9.0 | ISC |
| oidc-client-ts | 3.5.0 | Apache-2.0 |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| react-oidc-context | 3.3.1 | MIT |
| react-router-dom | 7.18.4 | MIT |

## npm (MCP server, `mcp/package.json` `dependencies`)

| Package | Version | License |
|---|---|---|
| @modelcontextprotocol/sdk | ~1.32.2 (1.32.2 in `package-lock.json`) | MIT |
| zod | ^4.6.7 (4.6.7 in `package-lock.json`) | MIT |

## Tools the deployment runs

`deployment/deploy.sh` runs the AWS CDK CLI with `npx --yes aws-cdk@2.1144.0` (Apache-2.0) and uses
[uv](https://github.com/astral-sh/uv) (Apache-2.0 or MIT) to build the Python bundles. Neither is
shipped in the deployed application.
