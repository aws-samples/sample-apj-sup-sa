---
name: import-plugins
description: Bring plugins the user already installed in Claude Code or Codex into Kiro, or import one straight from a marketplace GitHub URL. Use when the user mentions Claude Code plugins, Codex plugins, a plugin marketplace, or asks to bring, move, port or migrate their plugins to Kiro. Not for authoring a new plugin from loose files.
---

# Import Claude Code / Codex plugins into Kiro

A script does the conversion. You run it, read its report back to the user, and help with the one
decision it refuses to make: activation keywords. **Never edit plugin contents yourself** — the
script copies bodies byte-for-byte and stops if one differs, and that guarantee is the point of this
skill.

The script is at a fixed path. Set it once:

```bash
S=~/.kiro/powers/installed/plugin-importer/skills/import-plugins/scripts/kiro-port.py
```

If that path is missing, `ls ~/.kiro/powers/installed/*/skills/import-plugins/scripts/` to find it.
Never glob the home directory — it takes minutes and finds nothing useful.

## 1. See what there is

```bash
python3 $S scan                      # what Claude Code and Codex have installed
python3 $S scan --from <source>      # a marketplace or plugin repo: URL, owner/repo, or local path
```

`scan` prints each plugin's components and where it recommends putting them. Show that list grouped
by tool. Project-scoped Claude Code installs appear only when Kiro runs inside that project, matching
Claude Code's own visibility.

With `--from`, entries whose source points at another git repository print as `remote: <url>`. `port`
fetches only the ones you name, so always pass explicit names unless the marketplace is small.

**Ask which ones rather than defaulting to everything.** Each Power the user does not need is context
they pay for every session, and plugins carrying `node_modules` can exhaust the editor's file
descriptors — the script excludes those trees and warns past 2,000 files.

## 2. Port

```bash
python3 $S port <name>[@<market>] ...          # or --all
python3 $S port --from <source> <name>...
```

Follow the recommendation from `scan` unless the user wants otherwise:

| Plugin holds | Goes to |
|---|---|
| `commands/` or `.mcp.json` | a Power — only a Power can carry those |
| skills (hooks and agents may come along) | `~/.kiro/skills/`, plus `~/.kiro/hooks/` and `~/.kiro/agents/` |
| hooks or agents but no skills | a Power |
| Markdown guidance only | nothing is moved; report the `~/.kiro/steering/` path and stop |

`--as skills` and `--as power` override it. `--as skills` on a plugin with commands or MCP drops
those, and the script says so. `--slash` also exposes a Power's skills as `/name` slash commands
(symlinks under `~/.kiro/skills/`) — use it when the user wants to pick one skill out of a bundle
the way they did with `/command` in Claude Code, or asks for slash commands. `--project-local` writes into the current project's `.kiro/` instead of
installing globally. `--out DIR` only generates, which is how you turn a marketplace into a
Kiro-installable monorepo. `--smoke` checks that Kiro loads the result.

## 3. Report back, then offer keywords

For each plugin:

1. Say what came over and what the report marked `❌` or `⚠️`. **Do not soften the losses.** See
   `references/kiro-mapping.md` for what each one means.
2. `keywords` were derived from names only. Read the plugin's description and its skills'
   `description` lines, then append to the report:

   ```
   ## Suggested keywords
   Phrases the user would actually type when this Power should activate. Add them to
   plugin.json's keywords to apply.
   - commit       — requests like "commit this", "commit my changes"
   - open a PR    — …
   ```

   Three to six phrases in the user's language (Korean, English, or whatever they use), each with
   the situation it covers. **Recommend only** —
   do not write them into `plugin.json` unless asked.
3. Tell them to start a new session. Powers and skills load at session start.
4. If the plugin went into a Power (skills stayed inside it, not `~/.kiro/skills/`), mention that its
   skills resolve by plain-language request in a `kiro-cli --v3` session (a native `Kiro Powers` tool
   reads them by name) — but only there. In classic mode or `--no-interactive`, that tool isn't
   available and asking by name won't reliably find it; giving the skill's exact path
   (`~/.kiro/powers/installed/<plugin>/skills/<skill>/SKILL.md`) is the fallback that works everywhere.

## 4. Removing

```bash
python3 $S unport <name>
```

Removes everything the import installed — Power, skills, hooks, agents, supporting files — and leaves
the original plugin untouched.

## What the script guarantees

- Skill, command and agent bodies and hook scripts are copied byte-for-byte, asserted after the copy.
- The only body edit is `${CLAUDE_PLUGIN_ROOT}` → the installed path.
- `.claude-plugin/`, `commands/`, `agents/` stay in place, so the same directory still works as a
  Claude Code plugin.
- Anything Kiro has no equivalent for is reported, never approximated.
- Powers it did not create are never overwritten.
