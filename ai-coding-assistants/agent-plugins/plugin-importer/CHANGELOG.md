# Changelog

## 1.1.0

- `--slash` exposes a Power's skills as `/name` slash commands by symlinking each one into
  `~/.kiro/skills/`. Kiro follows the link, so nothing is copied and the Power's keyword activation
  keeps working alongside. Names already taken in `~/.kiro/skills/` are skipped and reported;
  `unport` removes only the links it made.
- A source that is already an Agent Plugin (root `plugin.json` with `$schema`) keeps its own
  manifest — the author's `keywords` and `version` are no longer replaced by name-derived ones — and
  its bodies are not run through `${CLAUDE_PLUGIN_ROOT}` substitution. This is also how the
  converter installs itself from a clone: `port --from . plugin-importer --as power`.
- `stdio` MCP servers from a Power's `mcp.json` are registered in `~/.kiro/settings/mcp.json` under
  `powers.mcpServers` as `power-<power>-<server>`, which is what kiro-cli actually reads; `unport`
  removes them. Remote servers are skipped and reported.
- Marketplace metadata is treated as untrusted. An entry's `name` must be a single path segment
  (`[\w.-]{1,64}`); a string `source` or a remote `path` must resolve inside its repository.
  Entries that fail either check are skipped with a warning, and the install directory is checked
  against its base before anything is copied. The `CLAUDE_PLUGIN_ROOT` prefix in generated hook
  commands is shell-quoted.
- `--project-local` installs are recorded in the project's `.kiro/.kiro-port-manifest.json` — skills,
  hooks, agents and the MCP server keys merged into `.kiro/settings/mcp.json`. `unport` run from that
  project removes exactly those, the staging copy under `.kiro/.ported/` is deleted after the install,
  and hook scripts the plugin ships are kept under `.kiro/.kiro-port-assets/<name>/` so the installed
  hooks still resolve.
- `unport` exits non-zero and changes nothing when it finds no record of the name, instead of
  printing `removed`.
- `--as skills` on a plugin with no `skills/` directory no longer crashes and leaves a half-built
  staging directory behind; it reports that there is nothing to place and points at `--as power`.
- `tests/regression-test.py` — 71 checks against synthetic fixtures in an isolated `KIRO_HOME`, no
  framework, no network.
- Flat-list `hooks.json` variants (`{"hooks": [{"event": ...}]}`) are now accepted alongside the
  nested form, without renaming event names — they carry over unchanged.
- Skills-only installs no longer silently drop everything when a folder name collides with an
  already-installed skill of a different plugin — if nothing could be copied, the report says so and
  suggests `--as power`.
- Symlinked files inside a plugin are copied by content, not re-linked — a relative symlink pointing
  outside the plugin directory (common in marketplaces with shared files) no longer breaks after the
  copy.
- A skill that references a sibling folder without a `SKILL.md` (shared assets, not itself a skill) now
  has that folder copied alongside it in skills-only installs.
- `${CLAUDE_PLUGIN_ROOT}` substitutions that point into an intermediate staging directory are
  redirected to the permanent asset path when that directory is removed after a skills-only install,
  instead of being left dangling.

## 1.0.0

First release.

- `scan` lists plugins installed through Claude Code or Codex, with each one's components and a
  recommended destination. `--from` reads a marketplace or plugin repository instead, following
  entries that point at another git repository.
- `port` converts and installs. Skill-bearing plugins go to `~/.kiro/skills/` with their hooks and
  agents alongside, so slash commands and hooks both keep working; plugins with `commands/` or MCP
  servers become Powers. `--as skills` / `--as power` override, `--project-local` writes into the
  current project, `--out` generates without installing.
- `unport` removes everything an import installed and leaves the original plugin untouched.
- Bodies are copied byte-for-byte and checked after the copy. The only substitution is
  `${CLAUDE_PLUGIN_ROOT}` → the installed path.
- Hooks are rewritten to Kiro's `v1` schema with tool matchers mapped. Fields Kiro has no equivalent
  for — `if`, `timeout`, `asyncRewake` — are reported rather than dropped silently, and `shell` is
  honoured by wrapping the command.
- `keywords` are derived from plugin and skill names only; anything beyond that is recommended in the
  report and never written without asking.
