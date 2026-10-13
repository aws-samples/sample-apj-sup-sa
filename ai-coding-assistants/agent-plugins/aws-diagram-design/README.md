# aws-diagram-design — Agent Plugin

**English** | [한국어](README.ko.md)

**Describe it in words; get an AWS diagram.**

An [Agent Plugins 1.0.0](https://agent-plugins.org/) package that teaches a coding agent (Kiro, Claude Code, or any client that reads `plugin.json` + `skills/`) to draw AWS-branded editorial diagrams as self-contained HTML — official [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/), Amazon Ember typography, the VPC / subnet / account container conventions — and export them to SVG/PNG.

> "Draw a VPC three-tier: ALB in public subnets, Fargate service in private, RDS Multi-AZ and ElastiCache in the data tier."

![VPC Three-Tier Web Service](docs/samples/vpc-three-tier.svg)

More samples in [`docs/samples/`](docs/samples/) (HTML sources included). GitHub blocks web fonts in SVG previews, so the samples render with the fallback face; open the HTML in a browser for Amazon Ember.

## What's in the plugin

| Component | Kind | What it does |
|---|---|---|
| `skills/aws-diagram-design` | skill | The design system: 27 visual types (architecture, IT current-state, flowchart, sequence, state, ER, timeline, swimlane, Gantt, data flow, medallion, …), AWS brand skin, 812 bundled icons, Amazon Ember fonts, self-check scripts. Auto-triggers on diagram requests in English and Korean. |
| `skills/import-drawio` | skill | Redraw an existing `.drawio` / `.drawio.png` / `.drawio.svg` at a chosen size, detail level, and audience. Extracts structure with `drawio_extract.py`; never copies source layout. |
| `skills/import-mermaid` | skill | Same for `.mmd` / `.mermaid` / fenced `mermaid` blocks in Markdown. |
| `skills/export-diagram` | skill | Export a generated HTML diagram to `.svg` and/or `.png` (Playwright, transparent background, 1×/2×/3×). |
| `skills/aws-live-architecture` | skill · **new** | Inventory a real account/region or one VPC **read-only** (Describe/List/Get only) and draw the as-is architecture. Runs with local credentials, or through the AWS MCP Server when there are none. |
| `mcp.json` | MCP server · optional | [AWS MCP Server](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html) over streamable HTTP (OAuth). Used by `aws-live-architecture` as a credential-free path, and by the agent to verify service names / regional availability against AWS documentation. The other four skills never need it. |

Layout (per the spec: manifest at the root, skills under `skills/`, MCP servers in `mcp.json`; everything else is documentation or a client extension):

```
aws-diagram-design/
├── plugin.json                  # Agent Plugins 1.0.0 manifest
├── mcp.json                     # Agent Plugins 1.0.0 MCP declaration (aws-mcp, optional)
├── skills/
│   ├── aws-diagram-design/      # SKILL.md · references/ (40 specs) · assets/ (templates, aws-icons/, fonts/) · scripts/
│   ├── import-drawio/
│   ├── import-mermaid/
│   ├── export-diagram/
│   └── aws-live-architecture/   # SKILL.md · scripts/aws_inventory.py
├── .claude-plugin/              # Claude Code manifest + marketplace catalog (client-specific)
├── .mcp.json                    # same aws-mcp server in Claude Code's format
├── examples/                    # sample .drawio / .mmd / .md inputs for the import skills
├── docs/samples/                # rendered sample diagrams (HTML + SVG)
├── LICENSE · THIRD_PARTY_LICENSES.md · CHANGELOG.md
```

## Install

### Kiro (IDE or CLI) — as a Power

Powers follow the Agent Plugins format, so this directory installs as-is:

1. Clone with sparse checkout (this is a monorepo):
   ```bash
   git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-apj-sup-sa.git
   cd sample-apj-sup-sa && git sparse-checkout set ai-coding-assistants/agent-plugins/aws-diagram-design
   ```
2. Kiro IDE: **Powers** panel → **Add Custom Power** → **Import power from a folder** → select `ai-coding-assistants/agent-plugins/aws-diagram-design`.
3. Kiro CLI 2.11+: the skills are exposed as `/aws-diagram-design`, `/import-drawio`, `/import-mermaid`, `/export-diagram`, `/aws-live-architecture`, and activate on keywords (`구성도`, `architecture diagram`, `drawio`, …).

Skill-only alternative (no MCP): copy or symlink `skills/*` into `~/.kiro/skills/`.

### Claude Code — as a plugin

```
/plugin marketplace add /path/to/sample-apj-sup-sa/ai-coding-assistants/agent-plugins/aws-diagram-design
/plugin install aws-diagram-design@aws-diagram-design
/reload-plugins
```

Skills appear as `/aws-diagram-design:export-diagram`, `/aws-diagram-design:import-drawio`, etc. The `.mcp.json` registers the same `aws-mcp` server; Claude Code starts the OAuth sign-in on first use.

### Any other Agent Plugins client

Point the client at this directory. It reads `plugin.json`, discovers the five skills under `skills/`, and — if it supports MCP — connects `aws-mcp` from `mcp.json`. A skills-only client simply ignores `mcp.json`.

### Optional prerequisites

| Feature | Needs |
|---|---|
| PNG / SVG export | `pip install playwright && playwright install chromium` (the skill tells you when it's missing; it never installs anything itself) |
| Amazon Ember in your local browser | `skills/aws-diagram-design/scripts/install_fonts.sh` — optional, generated HTML embeds the woff2 files |
| `aws-live-architecture`, local path | `boto3` + AWS credentials (`aws login`, SSO, or a named profile). A **read-only** profile is preferred. |
| `aws-live-architecture`, MCP path | Sign in when the client prompts (needs the `AWSMCPSignInOAuthAccessPolicy` managed policy on your IAM principal). For terminal agents that prefer SigV4, swap the server for `uvx mcp-proxy-for-aws … https://aws-mcp.us-east-1.api.aws/mcp` as documented in the AWS MCP Server guide. |

## Usage

Just ask, in English or Korean:

```
Draw the architecture for this project: EKS with ArgoCD, RDS Multi-AZ, ALB ingress.
Transit Gateway hub-and-spoke diagram, three accounts.
이 온보딩 프로세스를 스윔레인으로 정리해줘
주문 처리 시퀀스 다이어그램 — API Gateway, Lambda, SQS, DynamoDB
```

Explicit skill invocations:

```
/import-drawio legacy-arch.drawio --size=slide-16x9 --detail=simplified --audience=executive
/import-mermaid README.md --diagram=all
/export-diagram my-diagram.html --png-only --scale=3
/aws-live-architecture --region ap-northeast-2 --vpc vpc-0123456789abcdef0 --detail=faithful
```

Imports and the live inventory share four output dials — **format** (`html`/`svg`/`png`/`html+png`), **size** (`doc-inline`, `slide-16x9`, `social-og`, …), **detail** (`faithful`/`balanced`/`simplified`), **audience** (`engineer`/`mixed`/`executive`) — and always end with a *fidelity ledger* listing what was merged, collapsed, dropped, or (for the live inventory) skipped for lack of permission.

### About the live-inventory skill

`aws_inventory.py` covers VPC/subnets (public vs private via route tables), IGW/NAT, EC2 (fleets collapsed by `Name`), ALB/NLB and their targets, ECS/Fargate services, EKS, Lambda (+ event source mappings), RDS/Aurora, ElastiCache, DynamoDB, S3, SQS, SNS subscriptions, API Gateway (+ Lambda integrations), CloudFront origins, Kinesis, OpenSearch. Edges are **inferred from configuration**, not observed traffic, and the skill says so in the diagram legend and ledger. It is deliberately self-contained (boto3 only) so the identical file can run locally or inside the AWS MCP Server's sandboxed Python tool.

## Do you need the AWS MCP Server?

Short answer: **not for drawing.** Four of the five skills are pure prompt + local Python + bundled assets. The server is declared because it unlocks two things without any local setup:

1. A credential-free way to run the read-only inventory for `aws-live-architecture` (the script runs inside the server's sandbox with the signed-in principal).
2. Fact-checking while drawing — confirming a service name, whether it exists in `ap-northeast-2`, or what an icon should be called — against live AWS documentation instead of memory.

If you don't want it, delete `mcp.json` (and `.mcp.json`); nothing else changes. Don't run it alongside the older `aws-api-mcp-server` / `aws-knowledge-mcp-server` — AWS recommends replacing those with the managed server to avoid duplicate tools.

## License

MIT — see [LICENSE](LICENSE). Derivative of [masangbeom/aws-diagram-design](https://github.com/masangbeom/aws-diagram-design), which is based on [cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design). AWS Architecture Icons follow the [AWS icon terms](https://aws.amazon.com/architecture/icons/) (no recoloring or alteration); Amazon Ember follows the bundled licensing guidelines. Full notices in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
