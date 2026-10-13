# plugin-importer

**English** | [한국어](README.ko.md)

**The plugins you installed in Claude Code or Codex, running in Kiro — contents untouched.**

An [Agent Plugins 1.0.0](https://agent-plugins.org/) plugin for Kiro. Install it once, then ask for
your plugins. It reads what you installed through Claude Code (`/plugin install …`) or Codex
(`codex plugin add …`), copies each plugin byte-for-byte, adds the one file Kiro needs, and registers
the result. It also imports straight from a marketplace GitHub URL, for products published only in
Claude Code or Codex format.

> "Claude Code에서 쓰던 플러그인 전부 Kiro로 옮겨줘"
> "Bring `obra/superpowers-marketplace` into Kiro"

Both formats already share their *contents*: SKILL.md files, hook scripts and MCP definitions are the
same on either side. Only the packaging differs. Anthropic's packaging is not part of the Agent
Plugins standard, so a Claude Code plugin does not load in Kiro as it stands. This adds the packaging
and nothing else.

## Install

```bash
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa
git sparse-checkout set ai-coding-assistants/agent-plugins/plugin-importer
```

Then either of these registers it as a Power:

- **Kiro IDE**: **Powers** panel → **Add Custom Power** → **Import power from a folder** → select
  `ai-coding-assistants/agent-plugins/plugin-importer`. Pasting this directory's GitHub URL works too.
- **Kiro CLI only** (there is no `/powers add` in the CLI): let the converter install itself.

  ```bash
  cd ai-coding-assistants/agent-plugins/plugin-importer
  python3 skills/import-plugins/scripts/kiro-port.py port --from . plugin-importer --as power
  ```

Start a new session afterwards; Powers load at session start.

**Run Kiro with `kiro-cli --v3`.** Classic mode and `--no-interactive` sessions load Powers and skills
fine, but hooks silently do nothing in them — `/hooks` reports 0 either way. `--v3` is required for a
ported plugin's hooks to fire at all.

Requires `python3` (3.11+) and `git` on `PATH`. Built and exercised on macOS against kiro-cli
2.21.0; other platforms and versions have not been checked.

## Usage

Ask; you do not need the commands.

```
> Claude Code와 Codex에서 쓰던 플러그인 보여줘        # lists what is installed, changes nothing
> commit-commands, frontend-design 옮겨줘            # name the ones you want
> commit-commands 제거해줘                           # undo
```

Everything found in `~/.claude/plugins/` and `~/.codex/` is listed with its components and a
destination. Open a new session to use the imported plugins.

Claude Code **project-scoped** installs are offered only when Kiro runs inside that project, mirroring
Claude Code's own visibility. Say *"이 프로젝트에만"* to write into the project's `.kiro/` instead of
installing globally.

### From someone else's marketplace

A vendor tells your team to run `/plugin marketplace add acme/acme-plugins` and
`/plugin install acme-review@acme-plugins`. From Kiro that is one sentence:

```
> acme/acme-plugins 에서 acme-review 가져와줘
```

The importer fetches the repository, reads its `.claude-plugin/marketplace.json`, and converts and
registers the plugin you named. Entries pointing at another git repository are followed. Name the plugins
you want — a large marketplace has thousands of entries.

## Where a plugin lands

One question decides it: **does this work without a Power?** If it does, no Power gets built. A
Power only activates after a keyword match and costs the plugin its slash commands, so it is used
only where nothing else can carry the component.

- **Skills** already work — Kiro reads the Agent Skills standard directly. Straight to
  `~/.kiro/skills/`, no Power needed.
- **Hooks** fire on their own once the file sits in `~/.kiro/hooks/`, Power registration or not. So a
  plugin with both skills and hooks sends both down the skills path: it keeps its slash commands and
  its hooks still fire.
- **Commands** have no Kiro equivalent; they become skills, and a Power is the only place to put the
  result. Required.
- **MCP servers** connect only when their Power activates. Required.
- **Agents with no skills** need somewhere to live, so a Power is built to hold them.
- **Markdown guidance alone** is not moved at all. A Power activates on keywords; guidance is meant to
  always apply, so wrapping it in a Power would change what the author intended.

| Plugin holds | Destination | Why |
|---|---|---|
| `commands/` or `.mcp.json` | a Power | Only a Power can carry these |
| skills, with or without hooks and agents | `~/.kiro/skills/`, plus `~/.kiro/hooks/` and `~/.kiro/agents/` | Skills already work without a Power, and so do hooks — both slash commands and hooks survive |
| hooks or agents but no skills | a Power | Somewhere to hold them |
| Markdown guidance only | nothing moved; `~/.kiro/steering/` path only | A Power is conditional; steering is always-on — different jobs |

`--as skills` and `--as power` override this. A Power's skills are not slash commands; if you want
`/name` for them as well — the way a Claude Code bundle let you pick one command — add `--slash`, which
links each skill into `~/.kiro/skills/` without copying it. Guidance files are never converted automatically — a
steering file with `inclusion: always` is the closer match, since it stays always-on the way the
guidance did.

Bodies are copied byte-for-byte and the import aborts if one differs. The original
`.claude-plugin/`, `commands/` and `agents/` stay in place, so the same directory still works as a
Claude Code plugin. Anything Kiro has no equivalent for — `SessionEnd` and `UserPromptExpansion`
hooks, `argument-hint`, `.toml` commands, remote (`http`/`sse`) MCP servers, an agent's `tools` and
`model` limits — is reported rather than approximated.

**`references/kiro-mapping.md` has the per-component detail**, along with the Kiro behaviours worth
knowing before you go looking for a bug: skills inside a Power are not slash commands, remote MCP
servers in a Power are ignored, and a markdown agent applies only when the session starts with it.

## Keywords — the one thing you decide

Kiro activates a Power by **keywords**, not by slash command. The importer fills them from the plugin
and skill names and refuses to invent more: a keyword the author never chose would activate the Power
in situations the author never intended. After each import it suggests a few phrases with the
situation each covers, and asks before writing any of them into `plugin.json`. If something does not
activate after an import, this is almost always why.

## Contents

| Component | Kind | What it does |
|---|---|---|
| `skills/import-plugins` | skill | Runs the import, reads the report back, suggests keywords. |
| `skills/import-plugins/scripts/kiro-port.py` | script | The converter. Python 3.11+, standard library only. |
| `skills/import-plugins/references/kiro-mapping.md` | reference | Per-component mapping and Kiro behaviours. |
| `tests/regression-test.py` | tests | `python3 tests/regression-test.py` — ports synthetic fixtures into a temporary `KIRO_HOME`. No framework, no network. |

Running the converter directly, if you prefer:

```bash
S=~/.kiro/powers/installed/plugin-importer/skills/import-plugins/scripts/kiro-port.py
python3 $S scan                                   # what Claude Code / Codex have installed
python3 $S port superpowers commit-commands       # convert, install, register
python3 $S port --from acme/acme-plugins --all    # straight from a marketplace repo
python3 $S port eli5 --as skills                  # force a plain folder copy
python3 $S port ralph-loop --project-local        # into ./.kiro/ instead of globally
python3 $S port --all --out ./powers              # generate only, e.g. to publish a monorepo
python3 $S unport superpowers                     # remove
```

`--out` turns a Claude Code marketplace into a Kiro-installable monorepo: each generated directory is
a complete plugin that still works as a Claude Code plugin.

## License

MIT-0 — see [LICENSE](LICENSE).
