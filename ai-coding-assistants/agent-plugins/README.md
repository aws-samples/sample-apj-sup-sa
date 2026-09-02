# Agent Plugins

Plugins packaged in the vendor-neutral [Agent Plugins 1.0.0](https://agent-plugins.org/) format — a `plugin.json` manifest, Agent Skills under `skills/`, and optional MCP servers in `mcp.json`. Each directory installs as-is into Kiro (as a Power), Claude Code (as a plugin), or any client that implements the spec.

| Plugin | What it does |
|---|---|
| [aws-diagram-design](./aws-diagram-design) | AWS-branded editorial diagrams (27 types, official icons, Amazon Ember) as HTML/SVG/PNG; draw.io and Mermaid redraw; read-only current-state diagrams from a live account. |

Related: [kiro-powers](../kiro-powers) holds Powers in the earlier `POWER.md` format.
