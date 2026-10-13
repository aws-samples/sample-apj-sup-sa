# Changelog

## 2.0.0 — 2026-09-02

Repackaged as an [Agent Plugins 1.0.0](https://agent-plugins.org/) plugin (from masangbeom/aws-diagram-design 1.2.0).

- `plugin.json` now follows the closed 1.0.0 manifest schema; `mcp.json` added (AWS MCP Server, streamable HTTP).
- Claude Code slash commands (`commands/*.md`) became skills — `export-diagram`, `import-drawio`, `import-mermaid` — since the spec has no command component and skills are invocable in both Claude Code and Kiro.
- New skill `aws-live-architecture` with `scripts/aws_inventory.py`: read-only account/VPC inventory emitted in the same IR digest shape as the draw.io / Mermaid extractors, runnable locally or through the AWS MCP Server.
- Repo-level `scripts/verify-motion.py` and `install.sh fonts` moved into the skill (`scripts/verify-motion.py`, `scripts/install_fonts.sh`); test fixtures moved to `examples/`.
- `.claude-plugin/` and `.mcp.json` kept as Claude Code client extensions.

## 1.2.0 and earlier

See https://github.com/masangbeom/aws-diagram-design.
