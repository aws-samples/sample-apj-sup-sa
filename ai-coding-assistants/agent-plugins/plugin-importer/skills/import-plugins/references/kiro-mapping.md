# Where each component lands in Kiro

Read this when the user asks what happened to a specific component, or when the report shows a `❌`
you need to explain.

## What counts as "installed"

`scan` and `port` read exactly two files: `~/.claude/plugins/installed_plugins.json` and
`~/.codex/config.toml`. A name matches only if it appears there — this has nothing to do with whether
the plugin conforms to the Agent Plugins standard. Claude Code and Codex plugins never conform to that
standard on their own; converting them into something that does is the whole point of this tool. A
name failing to match means the name was never installed as a Claude Code or Codex plugin at all
(typo, a raw MCP server entry, a plugin that exists only in someone's description) — not that it lacks
Agent Plugins support.

Project-scoped entries (`scope: "project"` in `installed_plugins.json`) only match when the current
directory is inside that project, mirroring Claude Code's own visibility rule.

One thing never shows up in either file: a plugin loaded with `claude --plugin-dir <path>` or
`--plugin-url <url>` is session-only by design (Claude Code's own `--help` says so) and is never
written to `installed_plugins.json`. `scan`/`port` cannot see it, and there's no fallback for that
case — bringing that plugin's contents into Kiro means pointing at its directory directly with `--from`.

## Component map

| Claude Code / Codex | Kiro | Notes |
|---|---|---|
| `skills/<name>/SKILL.md` | `~/.kiro/skills/<name>/` or a Power's `skills/` | Same Agent Skills standard. Body is copied unchanged. |
| `commands/<name>.md` | `skills/<name>/SKILL.md` inside a Power | Frontmatter gains `name`; `allowed-tools` and `argument-hint` are dropped. Body unchanged. |
| `commands/<name>.toml` | — | Not Markdown. Not converted; reported. |
| `hooks/hooks.json` | `~/.kiro/hooks/<plugin>.json` | Rewritten to Kiro's `{"version":"v1","hooks":[…]}`. Scripts themselves are untouched. |
| `agents/<name>.md` | `~/.kiro/agents/<name>.md` | Body unchanged; frontmatter Kiro has no field for is dropped and reported. |
| `.mcp.json` | `mcp.json` in the Power root | Wrapped in `mcpServers` with the Agent Plugins `$schema`. Server entries unchanged. |
| `.claude-plugin/plugin.json` | `plugin.json` in the plugin root | New file. The original stays in place. |
| `${CLAUDE_PLUGIN_ROOT}` | the installed path | The only substitution ever made inside a body. |

## Hook events

Event names are copied as-is: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`, `SessionStart`.
Tool matchers are translated: `Bash` → `execute_bash`, `Read` → `fs_read`, `Write` / `Edit` →
`fs_write`.

Some plugins write `hooks.json` as a flat list of `{"event", "command", ...}` objects instead of the
nested `{event: [{matcher, hooks: [...]}]}` form — both are accepted.

**Hooks fire, but only in a `kiro-cli --v3` interactive session.** Checked directly: a hook installed
under `~/.kiro/hooks/` shows up in `/hooks` and actually runs (verified with a marker file written on
`PreToolUse`/`PostToolUse`/`UserPromptSubmit`/`Stop`) when the session is started with `kiro-cli --v3`
and left interactive. It reports 0 hooks and never fires under classic mode (`kiro-cli` without
`--v3`) or a `--no-interactive` headless invocation of either mode — those two are the cases to
recognise when someone says "the hook isn't firing."

[kiro.dev/docs/hooks/](https://kiro.dev/docs/hooks/) names the triggers differently (`PromptSubmit`,
`AgentStop`, plus IDE-only ones this CLI doesn't have) and describes `.kiro/hooks/` as project-local
only. Neither matches what was just verified — a hook file named with the *docs'* names
(`PromptSubmit`, `AgentStop`) sitting at the *docs'* path never fired, while the original Claude Code
names (`UserPromptSubmit`, `Stop`) at the global `~/.kiro/hooks/` path did. The docs page may describe
a different Kiro surface, or a spec ahead of what's shipped; either way, this converter follows what a
live `--v3` session actually does, not what that page says.

Fields Kiro has no equivalent for are reported rather than guessed at:

- `if` — the original ran only under that condition; the Kiro hook has no condition field, so it
  runs on every call the matcher catches — not "always" in the abstract, but *more often* than the
  author intended. When several `if`-scoped hooks shared one command (common for "review on commit,
  review on push, …"), they all collapse onto the same broad matcher: the report says how many, and
  that many now run **per matching call**, not per original condition. The converter keeps them
  distinguishable by folding the condition into the hook's name.
- `shell` — the command is wrapped as `<shell> -c '…'`, since dropping it would run a `.cmd` or
  similar file directly and fail.
- `timeout`, `asyncRewake`, `rewakeMessage` — dropped.
- `SessionEnd`, `UserPromptExpansion` — no Kiro event. Skipped, never approximated.

## Kiro behaviours that surprise people

- **A skill inside a Power is not a slash command.** `/name` works for a skill in `~/.kiro/skills/`;
  the same file inside a Power is not recognised. Ask for it by name instead ("run the X skill").
  This is why skill-bearing plugins go to `~/.kiro/skills/` by default. Kiro does follow a symlink
  in `~/.kiro/skills/`, which is what `--slash` uses: one link per skill pointing into the Power,
  so `/name` appears while the body stays in one place.
- **Hooks only fire in `kiro-cli --v3` interactive sessions.** Classic mode and `--no-interactive`
  headless sessions load the same `~/.kiro/hooks/*.json` file but `/hooks` reports 0 and nothing runs.
  If a hook seems to do nothing, check which mode the session was actually started in before assuming
  the conversion is wrong.
- **kiro-cli does not read a Power's `mcp.json` on its own.** The IDE installer copies each server
  into `~/.kiro/settings/mcp.json` under `powers.mcpServers` as `power-<power>-<server>`; without
  that entry the Power activates but "reports no tools". `port` writes those entries itself so a
  CLI-only install works, and `unport` removes them.
- **Remote MCP servers in a Power are ignored.** Only `stdio` servers (ones with a `command`) are
  registered; `http` and `sse` servers are skipped and reported. To use one, add it by hand to
  `~/.kiro/settings/mcp.json` at the top level.
- **Markdown agents do not appear in `kiro-cli agent list`, and `--agent <name>` cannot load one either**
  (`Error: no agent with name <name> found` — the classic agent registry is JSON-only). In `--v3` (KAS)
  sessions, `~/.kiro/agents/*.md` is loaded separately as a "user profile"; a plain-language request
  that matches the persona works without switching agents, but the profile is not selectable by name.
- **A Power's own skills resolve by name — but only in `kiro-cli --v3`.** In a `--v3` session, asking
  by plain description ("ponytail 사용법 알려줘", "shorty.py 오버엔지니어링된 부분만 리뷰해줘") reliably
  surfaces a native `Kiro Powers` tool call (`action=readSkill, powerName=<plugin>, skillName=<skill>`)
  that reads the right skill directly — verified across a Power's several skills in one session,
  including ones with no filename or keyword overlap with the prompt. Earlier testing that reported
  this as unreliable, or as requiring the exact `SKILL.md` path, was run in classic mode or
  `--no-interactive`, where that tool isn't available — the same distinction as the hooks note above.
  If a Power's skill still isn't found in an actual `--v3` interactive session, that's worth treating as
  a real finding, not an expected limitation.
- **Powers and skills load at session start.** Nothing appears until the next session.

## Why some plugins should not become Powers

A Power adds keyword activation and hides its skills from the slash list. That is worth it for
`commands/` (which only a Power can carry) and for MCP servers (which connect on activation). It is
not worth it for a plugin that only holds skills — the folder can sit in `~/.kiro/skills/` exactly as
it sat in `~/.claude/skills/`, and it keeps its slash commands.

A plugin that is only Markdown guidance is not a Power at all. Kiro's equivalent is a steering file
with `inclusion: always`, which is always on; a Power activates on keywords, so converting one would
change what the author intended.

## What is added, and what is left alone

| | Item | Detail |
|---|---|---|
| **Unchanged** | Skill, command and agent bodies; hook scripts; MCP server definitions; `scripts/` and `references/` inside skills | Byte-for-byte. The import aborts if a copied body differs. |
| **Unchanged** | `.claude-plugin/`, `commands/`, `agents/`, `hooks/` | Left in place, so the same directory still installs as a Claude Code plugin. |
| **Added** | `plugin.json` at the root | `$schema`, `name`, `version`, `description`, `author`, `license`, and name-derived `keywords`. |
| **Rewritten** | `commands/<x>.md` → `skills/<x>/SKILL.md` | Gains `name`; drops `allowed-tools` and `argument-hint`. Body identical. |
| **Rewritten** | `.mcp.json` → `mcp.json` | Wrapped in `mcpServers` with the Agent Plugins `$schema`. Entries identical. |
| **Rewritten** | `hooks/hooks.json` → `~/.kiro/hooks/<plugin>.json` | Kiro's `v1` schema. Scripts untouched. |
| **Rewritten** | `agents/<x>.md` → `~/.kiro/agents/<x>.md` | Body identical. Frontmatter Kiro has no field for is dropped and listed. |

Hooks and agents live outside the Power because the Agent Plugins standard has no portable slot for
them. `unport <name>` removes everything an import installed and leaves the original alone.

## File count

Kiro opens a file descriptor per file under a skill tree and does not close it
([kirodotdev/Kiro#10625](https://github.com/kirodotdev/Kiro/issues/10625)), so a plugin carrying
`node_modules` can break shell commands for the whole session. `node_modules`, `.venv`, `dist`,
`build` and friends are excluded from the copy, and a warning is printed past 2,000 files.
