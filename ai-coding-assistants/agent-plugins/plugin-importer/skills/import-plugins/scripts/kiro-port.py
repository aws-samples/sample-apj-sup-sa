#!/usr/bin/env python3
"""Port plugins installed from a Claude Code / Codex marketplace into a Kiro Power.

    kiro-port.py scan                      # list what CC/Codex have installed
    kiro-port.py port <name>[@<market>]... # convert + install/register under ~/.kiro
    kiro-port.py port --all
    kiro-port.py unport <name>...          # remove a ported Power
Options: --dry-run  --out DIR (generate a repo without installing)  --smoke (verify kiro-cli v3 loads it)

Principle: bodies stay byte-for-byte. What changes is packaging (plugin.json, one frontmatter
line, the hook schema) and location. Anything a human should decide (keywords, etc.) goes in the
REPORT. Anything outside the standard isn't deleted — it moves to dev.kiro/ and then
~/.kiro/hooks, ~/.kiro/agents.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HOME = Path.home()
KIRO = Path(os.environ.get("KIRO_HOME", HOME / ".kiro"))
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
HOOK_EVENTS = {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SessionStart"}
# Event names are carried over unchanged. kiro.dev/docs/hooks/ names them PromptSubmit/AgentStop,
# but installing both names side by side in a live `kiro-cli --v3` interactive session showed the
# original names (UserPromptSubmit/Stop) are the ones that actually fire — the docs page doesn't
# match what the CLI does.
HOOK_TOOL_MAP = {"Bash": "execute_bash", "Read": "fs_read", "Write": "fs_write", "Edit": "fs_write"}
CMD_DROP_KEYS = {"allowed-tools", "argument-hint"}
# Kiro opens a file descriptor per file under a skill tree and never closes it
# (kirodotdev/Kiro#10625); enough files exhausts the extension host's fds and kills the shell with
# `spawn EBADF`. Trees that aren't needed at runtime aren't copied.
EXCLUDE = shutil.ignore_patterns(
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    "dist", "build", ".next", ".turbo", "target", "*.log", "*.map", ".DS_Store")
FILE_WARN = 2000  # warn past this many files
AGENT_DROP_KEYS = {"tools", "model", "mcpServers", "hooks", "permissionMode", "color", "effort",
                   "initialPrompt", "disallowedTools", "skills", "memory", "background", "isolation", "maxTurns"}
MANIFEST = Path(os.environ.get("KIRO_HOME", HOME / ".kiro")) / ".kiro-port-manifest.json"
# kiro-cli's own slash commands (from `/help`, kiro-cli 2.21.1). A skill whose `name:` matches one
# of these is shadowed in the slash list — the built-in wins — so --slash warns about it.
KIRO_BUILTIN_SLASH = {
    "agent", "changelog", "chat", "clear", "code", "compact", "config", "context", "copy", "editor",
    "effort", "exit", "feedback", "help", "hooks", "knowledge", "mcp", "model", "paste", "plan",
    "powers", "prompts", "quit", "reply", "rewind", "session-id", "sessions", "settings", "spawn",
    "spec", "switch", "tangent", "title", "tools", "transcript", "upgrade-agent"}


def classify(src: Path) -> tuple[str, dict]:
    """Classify a plugin as skills-only / plugin (mixed) / guidance.

    Decides whether it needs wrapping in a Power. Skills alone are already covered by Kiro's own
    Agent Skills support, so a plain folder copy is enough; wrapping them in a Power adds a
    keyword-activation step on top.
    """
    n = {
        "skills": len([d for d in (src / "skills").iterdir() if (d / "SKILL.md").exists()])
                  if (src / "skills").is_dir() else 0,
        "commands": len(list((src / "commands").iterdir())) if (src / "commands").is_dir() else 0,
        "agents": len(list((src / "agents").glob("*.md"))) if (src / "agents").is_dir() else 0,
        "hooks": 0,
        "mcp": 1 if (src / ".mcp.json").exists() else 0,
    }
    mf = next((p for p in (src / ".claude-plugin/plugin.json", src / "plugin.json") if p.exists()), None)
    m = json.loads(mf.read_text()) if mf else {}
    if (src / "hooks/hooks.json").exists() or isinstance(m.get("hooks"), str):
        n["hooks"] = 1
    # Hooks and agents install outside the Power (~/.kiro/hooks, ~/.kiro/agents) either way, so
    # they don't require one. Confirmed: unregistering superpowers' Power and leaving only its
    # skills installed globally still lets its SessionStart hook fire. A Power is only required
    # for mcp.json (connects when the Power activates) and for commands (which become skills).
    if n["commands"] or n["mcp"]:
        return "plugin", n
    if n["skills"]:
        return "skills", n
    if n["hooks"] or n["agents"]:
        return "sidecar", n   # hooks/agents with no skills — just needs a Power shell to live in
    return "guidance", n


def global_skill_count() -> int:
    d = KIRO / "skills"
    return len([x for x in d.iterdir() if (x / "SKILL.md").exists()]) if d.is_dir() else 0


def recommend(kind: str) -> str:
    """Recommend an install mode from the classification. Skills alone have no reason to be wrapped in a Power."""
    return {"plugin": "power", "guidance": "steering", "skills": "skills", "sidecar": "power"}[kind]


# ------------------------------------------------------------------ reading install state

def scan_sources() -> list[dict]:
    """Plugin directories CC and Codex have installed locally."""
    out = []
    cc = HOME / ".claude/plugins/installed_plugins.json"
    if cc.exists():
        data = json.loads(cc.read_text())
        for key, installs in data.get("plugins", {}).items():
            name, _, market = key.partition("@")
            for inst in installs:
                p = Path(inst["installPath"])
                if p.is_dir():
                    out.append({"tool": "claude", "name": name, "market": market, "path": p,
                                "version": inst.get("version", ""), "scope": inst.get("scope", ""),
                                "project": inst.get("projectPath", "")})
    # Codex: [marketplaces.*] + [plugins."name@market"] in config.toml is the install state
    cfg_p = HOME / ".codex/config.toml"
    if cfg_p.exists():
        cfg = tomllib.loads(cfg_p.read_text())
        roots = {}
        for mk, m in cfg.get("marketplaces", {}).items():
            src = m.get("source", "")
            root = Path(src) if m.get("source_type") == "local" or src.startswith("/") else HOME / ".codex/.tmp/marketplaces" / mk
            roots[mk] = root
        for key, p in cfg.get("plugins", {}).items():
            name, _, mk = key.partition("@")
            root = roots.get(mk)
            if not root or not p.get("enabled", True):
                continue
            if mk.startswith("openai-"):  # bundled ChatGPT/Codex app plugins — runtime-coupled, not portable
                continue
            # the install lives at ~/.codex/plugins/cache/<market>/<name>/<version>/ (take the newest)
            vers = sorted((HOME / ".codex/plugins/cache" / mk / name).glob("*/"), key=os.path.getmtime)
            if vers:
                cands = [{"name": name, "path": vers[-1], "version": vers[-1].name}]
            else:  # no cache — fall back to the marketplace snapshot's local source
                cands = [c for c in marketplace_plugins(root) if c["name"] == name]
            for cand in cands:
                out.append({"tool": "codex", "name": name, "market": mk, "path": cand["path"],
                            "version": cand.get("version", ""), "scope": "user", "project": ""})
    return out


def safe_name(name) -> bool:
    """A plugin name is used as a single path segment under ~/.kiro and as a Power name.
    Anything else (absolute path, `..`, separators, shell metacharacters) is rejected —
    marketplace.json fetched with --from is untrusted input."""
    return isinstance(name, str) and name not in (".", "..") and re.fullmatch(r"[\w.-]{1,64}", name) is not None


def marketplace_plugins(root: Path, fetch: bool = False, only: set | None = None) -> list[dict]:
    """Plugin list inside a marketplace repo (.claude-plugin/marketplace.json).
    An entry's `source` is either a string (path inside the repo) or a dict pointing at another
    git repo (`url` / `git-subdir` / `github`). fetch=True also fetches that other repo.
    If there's no marketplace.json and the repo itself is a plugin, that's the one entry.
    Entries whose `name` isn't a safe path segment, or whose `source` resolves outside the
    fetched repo, are skipped with a warning — those values come from the remote repo."""
    mj = root / ".claude-plugin/marketplace.json"
    items = []
    root_r = root.resolve()
    if mj.exists():
        for e in json.loads(mj.read_text()).get("plugins", []):
            name = e.get("name")
            if not safe_name(name):
                print(f"   ⚠️ skipped a marketplace entry with an unsafe name: {name!r} (must match [\\w.-]{{1,64}})")
                continue
            src, p = e.get("source"), None
            if isinstance(src, str):
                p = (root / src).resolve()
                if not p.is_relative_to(root_r):
                    print(f"   ⚠️ {name}: `source` points outside the marketplace repo ({src!r}), skipped")
                    continue
                p = p if p.is_dir() else None
            elif isinstance(src, dict):
                if not fetch or (only is not None and e["name"] not in only):
                    # only fetch remote repos for entries explicitly named (large marketplaces run to thousands of entries)
                    items.append({"name": e["name"], "path": None, "version": str(e.get("version", "")),
                                  "description": e.get("description", ""), "remote": src})
                    continue
                repo = src.get("url") or src.get("repo") or ""
                if repo:
                    try:
                        fetched = fetch_source(repo, ref=src.get("ref")).resolve()
                        p = (fetched / (src.get("path") or "")).resolve()
                        if not p.is_relative_to(fetched):
                            print(f"   ⚠️ {name}: remote `path` points outside its repo ({src.get('path')!r}), skipped")
                            continue
                    except subprocess.CalledProcessError:
                        p = None
                    p = p if p and p.is_dir() else None
            if p:
                items.append({"name": name, "path": p, "version": str(e.get("version", "")),
                              "description": e.get("description", "")})
    elif (root / ".claude-plugin/plugin.json").exists() or (root / "plugin.json").exists():
        m = json.loads(next(p for p in (root / ".claude-plugin/plugin.json", root / "plugin.json") if p.exists()).read_text())
        name = m.get("name", root.name)
        if not safe_name(name):
            print(f"   ⚠️ plugin.json `name` {name!r} isn't a safe path segment, using the directory name `{root.name}`")
            name = root.name
        items.append({"name": name, "path": root, "version": str(m.get("version", "")),
                      "description": m.get("description", "")})
    return items


def fetch_source(src: str, ref: str | None = None) -> Path:
    """Local path | owner/repo | git URL -> local directory (shallow clone for git, cache reused)."""
    p = Path(src).expanduser()
    if p.is_dir():
        return p.resolve()
    url = src if "://" in src or src.startswith("git@") else f"https://github.com/{src}.git"
    dst = HOME / ".cache/kiro-port" / re.sub(r"[^\w.-]", "_", f"{src}@{ref}" if ref else src)
    if dst.is_dir():
        return dst
    cmd = ["git", "clone", "-q", "--depth", "1"] + (["--branch", ref] if ref else []) + [url, str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


# ------------------------------------------------------------------ frontmatter (a YAML subset)

def split_frontmatter(text: str):
    """(frontmatter lines, body) — body is returned as a string so it stays byte-identical."""
    if not text.startswith("---"):
        return [], text
    end = text.find("\n---", 3)
    if end < 0:
        return [], text
    fm = text[4:end].split("\n")
    body = text[end + 4:]
    return fm, body


def fm_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Group frontmatter into (key, original lines) blocks. Indented continuation lines attach to the preceding key."""
    blocks, cur = [], None
    for ln in lines:
        m = re.match(r"^([\w-]+)\s*:", ln)
        if m:
            cur = (m.group(1), [ln])
            blocks.append(cur)
        elif cur:
            cur[1].append(ln)
    return blocks


def rebuild_fm(blocks: list[tuple[str, list[str]]], body: str) -> str:
    lines = [ln for _, ls in blocks for ln in ls]
    return "---\n" + "\n".join(lines) + "\n---" + body


# ------------------------------------------------------------------ conversion

class Port:
    def __init__(self, src: Path, power_dir: Path, report: list[str]):
        self.src, self.dst, self.report = src, power_dir, report
        self.root_token = "${CLAUDE_PLUGIN_ROOT}"

    def sub_root(self, s: str) -> str:
        # The one body edit this tool ever makes: the install location is now fixed, so substitute the absolute path
        return s.replace(self.root_token, str(self.dst))

    def manifest(self) -> dict:
        for p in (self.src / ".claude-plugin/plugin.json", self.src / ".codex-plugin/plugin.json",
                  self.src / "plugin.json"):
            if p.exists():
                return json.loads(p.read_text())
        return {}

    def run(self, name: str, version_hint: str) -> None:
        m = self.manifest()
        if m.get("name") and m["name"] != name:
            # If the manifest's `name` differs from the registered (directory) name, Kiro silently
            # drops the whole Power list. The registered name wins; the original is noted in the report.
            self.report.append(f"- ⚠️ Source manifest `name` is `{m['name']}` but the Power was named `{name}` to match "
                               f"(the marketplace entry name and the manifest disagreed. Kiro refuses to load on a name clash or mismatch)")
        # 1) Copy the source as-is (preserves the CC format, so the same directory still works as a CC plugin).
        #    node_modules and similar are excluded — fd leaks kill Kiro's extension host (#10625).
        #    Symlinks are copied by content (symlinks=False) — a relative symlink inside a
        #    marketplace pointing outside the plugin (e.g. ../../../.claude/commands/x.md) would
        #    break once moved.
        shutil.copytree(self.src, self.dst, symlinks=False, ignore=EXCLUDE)
        skipped = [p.name for p in self.src.iterdir()
                   if p.is_dir() and p.name in ("node_modules", ".venv", "venv", "dist", "build", "target")]
        if skipped:
            self.report.append(f"- Excluded from copy: {skipped} (runtime dependencies/build output. Reinstall from source if needed)")
        nfiles = sum(1 for p in self.dst.rglob("*") if p.is_file())
        if nfiles > FILE_WARN:
            self.report.append(f"- ⚠️ {nfiles} files. Kiro keeps a file descriptor open per file under a skill tree "
                               f"(kirodotdev/Kiro#10625); this many can stall the shell with `spawn EBADF`")
        skills = self.dst / "skills"
        skill_names = [d.name for d in skills.iterdir() if (d / "SKILL.md").exists()] if skills.is_dir() else []
        # 2) Substitute ${CLAUDE_PLUGIN_ROOT} inside skills/ bodies only. Skipped when the source is
        #    already an Agent Plugin: those never use the token, and a file that merely mentions it
        #    (a script or doc) must not be rewritten.
        root_pj = self.src / "plugin.json"
        already_ap = root_pj.exists() and "agent-plugins.org" in str(json.loads(root_pj.read_text()).get("$schema", ""))
        for f in (skills.rglob("*") if skills.is_dir() and not already_ap else []):
            if f.is_file() and f.suffix in (".md", ".sh", ".py", ".json", ".yaml", ".yml"):
                t = f.read_text(errors="replace")
                if self.root_token in t:
                    f.write_text(self.sub_root(t))
                    self.report.append(f"- `{f.relative_to(self.dst)}`: `${{CLAUDE_PLUGIN_ROOT}}` → substituted with the install path")
        # 3) commands/*.md → skills/<name>/SKILL.md
        cmd_dir = self.dst / "commands"
        for cmd in sorted(cmd_dir.glob("*.md")) if cmd_dir.is_dir() else []:
            self.command_to_skill(cmd, skill_names)
        others = [p.name for p in cmd_dir.iterdir() if p.is_file() and p.suffix != ".md"] if cmd_dir.is_dir() else []
        if others:
            self.report.append(f"- ❌ Commands that aren't `.md` are not converted: {others}")
        # 4) plugin.json (root)
        existing = json.loads(root_pj.read_text()) if root_pj.exists() else {}
        if already_ap:
            # Already an Agent Plugin: the author's manifest is the source of truth — in particular
            # their keywords, which are the activation triggers. Only the name is aligned.
            pj = dict(existing)
            pj["name"] = name
            pj.setdefault("version", str(version_hint or "1.0.0"))
            pj.setdefault("keywords", sorted(set([name] + skill_names)))
            (self.dst / "plugin.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2) + "\n")
            self.report.append(f"- Source is already an Agent Plugin — kept its `plugin.json` "
                               f"({len(pj['keywords'])} keyword(s) as the author set them)")
        else:
            pj = {"$schema": PLUGIN_SCHEMA, "name": name,
                  "version": str(m.get("version") or version_hint or "1.0.0"),
                  "description": m.get("description", "")}
            for k in ("author", "license", "homepage", "repository"):
                if k in m:
                    pj[k] = m[k]
            pj["keywords"] = sorted(set([pj["name"]] + skill_names))
            (self.dst / "plugin.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2) + "\n")
            self.report.append(f"- Generated `plugin.json`. `keywords` were derived **from names only**: "
                               f"{pj['keywords']} — these are activation triggers, edit directly if you need more")
            if not m.get("version"):
                self.report.append("- Source had no `version` → defaulted to `1.0.0`")
        # 5) .mcp.json → mcp.json
        mcp_src = self.dst / ".mcp.json"
        if mcp_src.exists():
            raw = json.loads(self.sub_root(mcp_src.read_text()))
            servers = raw.get("mcpServers", raw)
            (self.dst / "mcp.json").write_text(json.dumps(
                {"$schema": MCP_SCHEMA, "mcpServers": servers}, ensure_ascii=False, indent=2) + "\n")
            self.report.append(f"- Generated `mcp.json` (server definitions unchanged): {list(servers)}")
            # Confirmed (kiro-cli 2.21.0): stdio servers in a Power's mcp.json register normally,
            # but remote http/sse servers are silently ignored (no error either).
            remote = [n for n, s in servers.items()
                      if isinstance(s, dict) and s.get("type") in ("http", "sse", "streamable-http")]
            if remote:
                self.report.append(
                    f"- ❌ Remote MCP server(s) {remote} do not load from a Power (and produce no error). "
                    f"To use them, move the definition into `~/.kiro/settings/mcp.json` directly — stdio servers work as-is")
        # 6) hooks → dev.kiro/hooks/<name>.json
        hooks_file = self.dst / "hooks/hooks.json"
        if not hooks_file.exists() and isinstance(m.get("hooks"), str):
            hooks_file = self.dst / m["hooks"]
        if hooks_file.exists():
            self.convert_hooks(hooks_file, pj["name"])
        # 7) agents → dev.kiro/agents/<name>.md
        for ag in sorted((self.dst / "agents").glob("*.md")) if (self.dst / "agents").is_dir() else []:
            self.convert_agent(ag)

    def command_to_skill(self, cmd: Path, skill_names: list[str]) -> None:
        sname = cmd.stem
        target = self.dst / "skills" / sname / "SKILL.md"
        if sname in skill_names:
            self.report.append(f"- ⚠️ Command `{sname}` skipped — a skill with the same name already exists")
            return
        text = cmd.read_text(errors="replace")
        fm, body = split_frontmatter(text)
        blocks = fm_blocks(fm)
        kept = [(k, ls) for k, ls in blocks if k not in CMD_DROP_KEYS]
        dropped = [k for k, _ in blocks if k in CMD_DROP_KEYS]
        if not any(k == "name" for k, _ in kept):
            kept.insert(0, ("name", [f"name: {sname}"]))
        if not any(k == "description" for k, _ in kept):
            kept.append(("description", [f"description: {sname}"]))
            self.report.append(f"- Command `{sname}`: had no `description`, filled in with the name — worth checking")
        target.parent.mkdir(parents=True)
        target.write_text(rebuild_fm(kept, self.sub_root(body)))
        assert split_frontmatter(target.read_text())[1] == self.sub_root(body), "body mismatch"
        note = f" (dropped from frontmatter: {', '.join(dropped)})" if dropped else ""
        self.report.append(f"- Command `{sname}.md` → `skills/{sname}/SKILL.md`, body unchanged{note}")
        skill_names.append(sname)

    def convert_hooks(self, hooks_file: Path, pname: str) -> None:
        """Claude Code hooks.json → Kiro's v1 schema.

        Fields Kiro doesn't know about (`if`, `asyncRewake`, `timeout`, etc.) aren't dropped
        silently — they're reported. `if` in particular is what lets the same command be
        registered multiple times under different conditions; losing it makes the result look
        like duplicate copies of one hook, with the conditional branching gone without a trace.
        """
        raw = json.loads(hooks_file.read_text())
        raw_hooks = raw.get("hooks", {})
        if isinstance(raw_hooks, list):
            # Some plugins write hooks as a flat list of {event,command,...} objects instead of
            # nested matcher/hooks arrays. Normalize into the {event: [{matcher, hooks:[{type,command,...}]}]}
            # shape the rest of this logic expects.
            grouped: dict = {}
            for item in raw_hooks:
                ev = item["event"]
                h = {k: v for k, v in item.items() if k != "event"}
                h.setdefault("type", "command")
                grouped.setdefault(ev, []).append({"hooks": [h]})
            raw_hooks = grouped
        out, skipped, dropped = [], [], []
        for event, entries in raw_hooks.items():
            if event not in HOOK_EVENTS:
                skipped.append(event)
                continue
            for i, entry in enumerate(entries):
                matcher = entry.get("matcher")
                if matcher:
                    parts = [HOOK_TOOL_MAP.get(p, p) for p in re.split(r"\|", matcher)]
                    matcher = "|".join(dict.fromkeys(parts))
                for j, h in enumerate(entry.get("hooks", [])):
                    if h.get("type", "command") != "command":
                        skipped.append(f"{event}[{i}].hooks[{j}] type={h.get('type')}")
                        continue
                    cmd = f"CLAUDE_PLUGIN_ROOT={shlex.quote(str(self.dst))} " + self.sub_root(h["command"])
                    # `shell` says which shell to run this command through. Kiro's hook has no
                    # matching field, so the command itself is wrapped in that shell — dropping it
                    # would run a `.cmd`-style file directly and fail.
                    sh = h.get("shell")
                    if sh:
                        cmd = f"{sh} -c {shlex.quote(cmd)}"
                    # Fields Kiro has nothing to hold. Carried in the name for disambiguation and noted in the report.
                    extra = {k: v for k, v in h.items() if k not in ("type", "command", "shell")}
                    label = re.sub(r"[^\w.-]+", "-", str(extra.get("if", ""))).strip("-").lower()
                    name = f"{pname}-{event.lower()}-{i}-{j}" + (f"-{label}" if label else "")
                    hook = {"name": name[:80], "trigger": event,
                            "action": {"type": "command", "command": cmd}}
                    if matcher:
                        hook["matcher"] = matcher
                    out.append(hook)
                    if extra:
                        dropped.append((name[:80], extra))
        if out:
            d = self.dst / "dev.kiro/hooks"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{pname}.json").write_text(json.dumps({"version": "v1", "hooks": out}, ensure_ascii=False, indent=2) + "\n")
            self.report.append(f"- {len(out)} hook(s) → `dev.kiro/hooks/{pname}.json` (copied to `~/.kiro/hooks/` on install)")
            self.report.append(
                "  ⚠️ Only fires in a `kiro-cli --v3` interactive session (confirmed directly). In classic mode "
                "(`kiro-cli` without `--v3`) or a `--no-interactive` headless session, hooks never load and "
                "`/hooks` reports 0."
            )
        # If several hooks that lost their `if` share the same trigger+matcher, what used to run
        # once per distinct condition now collapses onto the same broad match and runs that many
        # times per call. Report that multiplier.
        if_dropped = [h for h in out if any(n == h["name"] for n, e in dropped if "if" in e)]
        overlap = {}
        for h in if_dropped:
            key = (h["trigger"], h.get("matcher"), h["action"]["command"])
            overlap.setdefault(key, []).append(h["name"])
        for name, extra in dropped:
            keys = ", ".join(f"`{k}`" for k in extra)
            self.report.append(f"- ❌ `{name}`: fields Kiro has no equivalent for were not carried over — {keys}")
            if "if" in extra:
                n_same = next((len(v) for v in overlap.values() if name in v), 1)
                self.report.append(f"    The original only ran when `{extra['if']}` held. Kiro's hook has no condition field, so it "
                                   f"**runs on every call the matcher catches**" +
                                   (f" — {n_same} identical commands now sit at this spot, so one call runs it {n_same} times"
                                    if n_same > 1 else ""))
        if skipped:
            self.report.append(f"- ❌ Hooks with no Kiro equivalent were not converted: {skipped}")

    def convert_agent(self, ag: Path) -> None:
        fm, body = split_frontmatter(ag.read_text(errors="replace"))
        blocks = fm_blocks(fm)
        kept = [(k, ls) for k, ls in blocks if k not in AGENT_DROP_KEYS]
        dropped = [k for k, _ in blocks if k in AGENT_DROP_KEYS]
        d = self.dst / "dev.kiro/agents"
        d.mkdir(parents=True, exist_ok=True)
        (d / ag.name).write_text(rebuild_fm(kept, self.sub_root(body)))
        note = f" (dropped from frontmatter: {', '.join(dropped)} — no Kiro md-agent equivalent)" if dropped else ""
        self.report.append(f"- Agent `{ag.name}` → `dev.kiro/agents/`, body unchanged{note}. Copied to `~/.kiro/agents/` on install")


# ------------------------------------------------------------------ Kiro install/register

def _load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def _save(p: Path, data) -> None:
    if p.exists():
        shutil.copy(p, p.with_suffix(p.suffix + ".bak"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _manifest_put(name: str, entry: dict) -> None:
    m = _load(MANIFEST, {})
    m[name] = entry
    _save(MANIFEST, m)


def install_skills_only(power_dir: Path, report: list[str], name: str) -> None:
    """Skills go to ~/.kiro/skills/; hooks and agents stay outside the Power, in their own place.

    Since it isn't wrapped in a Power, skills are callable by slash (`/skill-name`). Hooks
    installed under ~/.kiro/hooks/ fire regardless of Power registration, so they're kept too
    (confirmed with superpowers).
    """
    dst_root = KIRO / "skills"
    dst_root.mkdir(parents=True, exist_ok=True)
    placed = []
    skill_dirs = sorted((power_dir / "skills").iterdir()) if (power_dir / "skills").is_dir() else []
    if not skill_dirs:
        report.append("- ❌ Nothing to place as skills — this plugin has no `skills/` directory. "
                       "Use `--as power` (commands, MCP servers and agents can only live in a Power)")
    for sk in skill_dirs:
        if not (sk / "SKILL.md").exists():
            continue
        dst = dst_root / sk.name
        if dst.exists():
            report.append(f"- ⚠️ `{dst}` already exists, skipped (a skill with this name)")
            continue
        shutil.copytree(sk, dst)
        placed.append(sk.name)
    report.append(f"- {len(placed)} skill(s) → `{dst_root}`: {placed}")
    # A sibling folder that isn't itself a skill (no SKILL.md) may be referenced by a skill via a
    # relative path (../folder-name/) or ${CLAUDE_PLUGIN_ROOT}/skills/folder-name — that folder has
    # to move too or the reference breaks. Skills/ is flat, so the relative path still resolves —
    # only the folder itself needs copying.
    shared_dirs = [d for d in skill_dirs if d.is_dir() and not (d / "SKILL.md").exists()]
    shared_copied = []
    for shared in shared_dirs:
        referenced = any(
            shared.name in (dst_root / p / "SKILL.md").read_text(errors="ignore")
            for p in placed
        )
        if not referenced:
            continue
        dst = dst_root / shared.name
        if dst.exists():
            report.append(f"- ⚠️ `{dst}` already exists, shared folder skipped (a referencing skill may break)")
            continue
        shutil.copytree(shared, dst)
        shared_copied.append(shared.name)
    if shared_copied:
        report.append(f"- Also moved {len(shared_copied)} shared folder(s) referenced by skills: {shared_copied}")
    total_src = len([sk for sk in skill_dirs if (sk / "SKILL.md").exists()])
    if total_src and not placed:
        report.append("- ❌ No skills were moved — all of them were skipped on a name clash. "
                       "Same folder name doesn't mean same content across plugins "
                       "(e.g. `api-patterns` shows up under several plugins, each with different content). "
                       "Re-run with `--as power` to keep it separate inside a Power, with no name collision")
    report.append("- Not wrapped in a Power. Skills are callable by `/name` slash, and Kiro's default skill matching also applies")
    hooks, agents = [], []
    hook_files = list((power_dir / "dev.kiro/hooks").glob("*.json"))
    # ${CLAUDE_PLUGIN_ROOT} was already substituted with power_dir's absolute path back in step 2
    # of Port.run. If a skill body references that path outside skills/ (references/, scripts/,
    # etc.), power_dir is about to be deleted (the caller rmtrees it) — that tree needs preserving,
    # and the path rewritten, even when there are no hooks.
    needs_assets = bool(hook_files) or any(
        str(power_dir) in (dst_root / p / "SKILL.md").read_text(errors="ignore")
        for p in placed
    )
    asset_root = None
    if needs_assets:
        asset_root = KIRO / ".kiro-port-assets" / name
        if asset_root.exists():
            shutil.rmtree(asset_root)
        asset_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(power_dir, asset_root, symlinks=True,
                        ignore=shutil.ignore_patterns("dev.kiro", "PORT-REPORT.md"))
        report.append(f"- Preserved referenced files at `{asset_root}`")
        for p in placed:
            skill_md = dst_root / p / "SKILL.md"
            body = skill_md.read_text(errors="ignore")
            new_body = body.replace(str(power_dir), str(asset_root))
            if new_body != body:
                skill_md.write_text(new_body)
    for hook in hook_files:
        dst = KIRO / "hooks" / hook.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        body = hook.read_text()
        if asset_root:
            body = body.replace(str(power_dir), str(asset_root))
        dst.write_text(body); hooks.append(hook.name)
        report.append(f"- Hook installed: `{dst}` (fires regardless of Power registration)")
    for ag in (power_dir / "dev.kiro/agents").glob("*.md"):
        dst = KIRO / "agents" / ag.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            report.append(f"- ⚠️ `{dst}` already exists, skipped")
            continue
        shutil.copy(ag, dst); agents.append(ag.name)
        report.append(f"- Agent installed: `{dst}` (start with `kiro-cli --v3 --agent {ag.stem}` for it to apply)")
    _manifest_put(name, {"mode": "skills", "skills": placed, "hooks": hooks, "agents": agents})


def project_manifest(project: Path) -> Path:
    """Project-local installs are recorded inside the project, so `unport` run from that project
    can undo exactly what was written there — the global manifest never sees them."""
    return project / ".kiro/.kiro-port-manifest.json"


def install_project_local(power_dir: Path, project: Path, report: list[str], name: str) -> None:
    """Unpack into the project's .kiro/ instead of a Power (matches CC's project scope).
    Everything written is recorded in the project manifest for `unport`."""
    k = project / ".kiro"
    skills, hooks, agents, mcp_keys = [], [], [], []
    for sk in (power_dir / "skills").iterdir() if (power_dir / "skills").is_dir() else []:
        dst = k / "skills" / sk.name
        if dst.exists():
            report.append(f"- ⚠️ `{dst}` already exists, skipped")
            continue
        shutil.copytree(sk, dst)
        skills.append(sk.name)
    report.append(f"- Skills → `{k / 'skills'}`: {skills}")
    # ${CLAUDE_PLUGIN_ROOT} was substituted with the staging path, which the caller deletes.
    # Anything that still points there (hook scripts, references/) is kept under .kiro and rewritten.
    hook_files = list((power_dir / "dev.kiro/hooks").glob("*.json"))
    needs_assets = bool(hook_files) or any(
        str(power_dir) in (k / "skills" / s / "SKILL.md").read_text(errors="ignore") for s in skills)
    asset_root = None
    if needs_assets:
        asset_root = k / ".kiro-port-assets" / name
        if asset_root.exists():
            shutil.rmtree(asset_root)
        asset_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(power_dir, asset_root, ignore=shutil.ignore_patterns("dev.kiro", "PORT-REPORT.md"))
        report.append(f"- Preserved referenced files at `{asset_root}`")
        for s in skills:
            p = k / "skills" / s / "SKILL.md"
            body = p.read_text(errors="ignore")
            if str(power_dir) in body:
                p.write_text(body.replace(str(power_dir), str(asset_root)))
    for hook in hook_files:
        (k / "hooks").mkdir(parents=True, exist_ok=True)
        body = hook.read_text()
        if asset_root:
            body = body.replace(str(power_dir), str(asset_root))
        (k / "hooks" / hook.name).write_text(body)
        hooks.append(hook.name)
        report.append(f"- Hook → `{k / 'hooks' / hook.name}`")
    for ag in (power_dir / "dev.kiro/agents").glob("*.md"):
        (k / "agents").mkdir(parents=True, exist_ok=True)
        if (k / "agents" / ag.name).exists():
            report.append(f"- ⚠️ `{k / 'agents' / ag.name}` already exists, skipped")
            continue
        shutil.copy(ag, k / "agents" / ag.name)
        agents.append(ag.name)
        report.append(f"- Agent → `{k / 'agents' / ag.name}`")
    mcp = power_dir / "mcp.json"
    if mcp.exists():
        target = k / "settings/mcp.json"
        cur = _load(target, {"mcpServers": {}})
        servers = cur.setdefault("mcpServers", {})
        for key, val in json.loads(mcp.read_text())["mcpServers"].items():
            if key in servers:
                report.append(f"- ⚠️ MCP server `{key}` already defined in `{target}`, left as is")
                continue
            servers[key] = val
            mcp_keys.append(key)
        _save(target, cur)
        report.append(f"- MCP → merged into `{target}`: {mcp_keys}")
    man_p = project_manifest(project)
    man = _load(man_p, {})
    man[name] = {"mode": "project", "skills": skills, "hooks": hooks, "agents": agents, "mcp": mcp_keys,
                 "assets": asset_root is not None}
    _save(man_p, man)
    report.append("- Note: project-local isn't a Power, so there's no keywords activation. Skills work through Kiro's default skill matching")


def unregister_project(name: str, project: Path) -> bool:
    """Undo a --project-local install recorded in the project manifest. Returns whether one was found."""
    man_p = project_manifest(project)
    man = _load(man_p, {})
    ent = man.get(name)
    if not ent:
        return False
    k = project / ".kiro"
    for sk in ent.get("skills", []):
        shutil.rmtree(k / "skills" / sk, ignore_errors=True)
    for h in ent.get("hooks", []):
        (k / "hooks" / h).unlink(missing_ok=True)
    for a in ent.get("agents", []):
        (k / "agents" / a).unlink(missing_ok=True)
    shutil.rmtree(k / ".kiro-port-assets" / name, ignore_errors=True)
    if ent.get("mcp"):
        target = k / "settings/mcp.json"
        cur = _load(target, {"mcpServers": {}})
        for key in ent["mcp"]:
            cur.get("mcpServers", {}).pop(key, None)
        _save(target, cur)
    (k / ".kiro-port-reports" / f"{name}.md").unlink(missing_ok=True)
    del man[name]
    if man:
        _save(man_p, man)
    else:
        man_p.unlink(missing_ok=True)
    print(f"removed {name} from {k} ({len(ent.get('skills', []))} skill(s), "
          f"{len(ent.get('hooks', []))} hook(s), {len(ent.get('mcp', []))} MCP server(s))")
    return True


def register(name: str, power_dir: Path, report: list[str], slash: bool = False) -> None:
    reg_p = KIRO / "powers/registries/user-added.json"
    reg = _load(reg_p, {"powers": []})
    reg["powers"] = [p for p in reg["powers"] if p["name"] != name]
    reg["powers"].append({"name": name, "description": f"Ported from Claude Code plugin ({power_dir})",
                          "source": {"type": "local", "path": str(power_dir)}, "autoInstall": False})
    _save(reg_p, reg)
    inst_p = KIRO / "powers/installed.json"
    inst = _load(inst_p, {"version": "1.0.0", "installedPowers": [], "dismissedAutoInstalls": []})
    if not any(p["name"] == name for p in inst["installedPowers"]):
        inst["installedPowers"].append({"name": name, "registryId": "user-added"})
    _save(inst_p, inst)
    # MCP servers: kiro-cli doesn't read a Power's mcp.json at runtime — the IDE installer merges it
    # into ~/.kiro/settings/mcp.json under powers.mcpServers as `power-<power>-<server>`. Without
    # that entry the Power activates but "reports no tools". Done here so a CLI-only install works.
    # Only stdio servers: Kiro never starts remote (http/sse) ones from a Power.
    mcp = power_dir / "mcp.json"
    if mcp.exists():
        settings = KIRO / "settings/mcp.json"
        cur = _load(settings, {})
        ps = cur.setdefault("powers", {}).setdefault("mcpServers", {})
        added = []
        for key, srv in json.loads(mcp.read_text()).get("mcpServers", {}).items():
            if not srv.get("command"):
                continue
            ent = {k: v for k, v in srv.items() if k != "type"}
            ent.setdefault("disabled", False)
            ps[f"power-{name}-{key}"] = ent
            added.append(key)
        if added:
            _save(settings, cur)
            report.append(f"- MCP server(s) registered in `{settings}` (powers.mcpServers): {added} — starts when the Power activates")
    # Things that have to live outside the Power
    for hook in (power_dir / "dev.kiro/hooks").glob("*.json"):
        dst = KIRO / "hooks" / hook.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(hook, dst)
        report.append(f"- Hook installed: `{dst}`")
    for ag in (power_dir / "dev.kiro/agents").glob("*.md"):
        dst = KIRO / "agents" / ag.name
        if dst.exists():
            report.append(f"- ⚠️ `{dst}` already exists, skipped")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ag, dst)
        report.append(f"- Agent installed: `{dst}`")
    if slash:
        # Kiro only offers `/name` for skills under ~/.kiro/skills/. A symlink there is enough —
        # Kiro follows it, the skill body stays in the Power (one copy, no extra fds), and the
        # Power's own keyword activation keeps working alongside.
        linked, slash_names, clashed, shadowed = [], [], [], []
        (KIRO / "skills").mkdir(parents=True, exist_ok=True)
        for sk in sorted((power_dir / "skills").iterdir()) if (power_dir / "skills").is_dir() else []:
            if not (sk / "SKILL.md").exists():
                continue
            link = KIRO / "skills" / sk.name
            if link.is_symlink() and str(os.readlink(link)).startswith(str(power_dir)):
                link.unlink()  # a link into this same Power from an earlier install — take it over
            elif link.exists() or link.is_symlink():
                clashed.append(sk.name)
                continue
            link.symlink_to(sk)
            linked.append(sk.name)
            # Kiro names the slash command after the SKILL.md `name:` field, not the directory
            mt = re.search(r"^name:\s*(\S+)", (sk / "SKILL.md").read_text(errors="ignore"), re.M)
            sname = mt.group(1) if mt else sk.name
            slash_names.append(sname)
            if sname in KIRO_BUILTIN_SLASH:
                shadowed.append(sname)
        if linked:
            report.append(f"- Slash commands: {['/' + n for n in slash_names]} → symlinks in `{KIRO / 'skills'}` pointing into the Power")
        if shadowed:
            report.append(f"- ⚠️ {['/' + n for n in shadowed]} collide with Kiro's own built-in commands and won't show up in the "
                          f"slash list — Kiro's built-in wins. Still reachable by asking in plain language")
        if clashed:
            report.append(f"- ⚠️ Not exposed as slash commands, a skill with that name already exists: {clashed}")
        _manifest_put(name, {"mode": "power", "slash": linked})


def unregister(name: str, cwd: Path) -> bool:
    """Remove everything an import installed. Checks the current project's manifest first
    (--project-local), then the global manifest (skills mode), then the Power directory.
    Returns False — and touches nothing — when no record of `name` exists anywhere."""
    if unregister_project(name, cwd):
        return True
    man = _load(MANIFEST, {})
    ent = man.get(name)
    if ent and ent.get("mode") == "skills":
        for sk in ent.get("skills", []):
            d = KIRO / "skills" / sk
            if d.is_dir():
                shutil.rmtree(d)
        for h in ent.get("hooks", []):
            (KIRO / "hooks" / h).unlink(missing_ok=True)
        shutil.rmtree(KIRO / ".kiro-port-assets" / name, ignore_errors=True)
        for a in ent.get("agents", []):
            (KIRO / "agents" / a).unlink(missing_ok=True)
        (KIRO / ".kiro-port-reports" / f"{name}.md").unlink(missing_ok=True)
        del man[name]
        _save(MANIFEST, man)
        print(f"removed {name} ({len(ent.get('skills', []))} skill(s))")
        return True
    power_dir = KIRO / "powers/installed" / name
    reg_p = KIRO / "powers/registries/user-added.json"
    inst_p = KIRO / "powers/installed.json"
    reg = _load(reg_p, {"powers": []})
    inst = _load(inst_p, {"installedPowers": []})
    registered = any(p.get("name") == name for p in reg["powers"]) or \
        any(p.get("name") == name for p in inst["installedPowers"])
    if not power_dir.is_dir() and not registered and not ent:
        print(f"nothing to remove: no import named `{name}` in this project, in the global manifest, "
              f"or under {KIRO / 'powers/installed'}")
        return False
    if ent:
        for sk in ent.get("slash", []):
            link = KIRO / "skills" / sk
            # only ever remove a symlink that points into this Power — never a real skill directory
            if link.is_symlink() and str(link.resolve()).startswith(str(power_dir.resolve())):
                link.unlink()
        del man[name]
        _save(MANIFEST, man)
    if power_dir.is_dir():
        for hook in (power_dir / "dev.kiro/hooks").glob("*.json"):
            (KIRO / "hooks" / hook.name).unlink(missing_ok=True)
        for ag in (power_dir / "dev.kiro/agents").glob("*.md"):
            (KIRO / "agents" / ag.name).unlink(missing_ok=True)
        shutil.rmtree(power_dir)
    reg["powers"] = [p for p in reg["powers"] if p.get("name") != name]
    _save(reg_p, reg)
    inst["installedPowers"] = [p for p in inst["installedPowers"] if p.get("name") != name]
    _save(inst_p, inst)
    settings = KIRO / "settings/mcp.json"
    if settings.exists():
        cur = _load(settings, {})
        ps = cur.get("powers", {}).get("mcpServers", {})
        mine = [k for k in ps if k.startswith(f"power-{name}-")]
        for k in mine:
            del ps[k]
        if mine:
            _save(settings, cur)
    print(f"removed {name}")
    return True


def smoke(names: list[str]) -> None:
    """Confirm kiro-cli v3 actually loads it. Needs a TTY, so it's wrapped in script(1)."""
    prompt = "Do not use any tools. List the names of all installed powers you can see, one per line, then stop."
    log = Path("/tmp/kiro-port-smoke.txt")
    subprocess.run(["script", "-q", str(log), "kiro-cli", "chat", "--v3", "--no-interactive", prompt],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240)
    seen = log.read_text(errors="replace")
    for n in names:
        print(f"  smoke {n}: {'LOADED' if n in seen else 'NOT SEEN'}")
    pl = sorted((KIRO / "logs").glob("*/powers.log"))
    if pl:
        fails = [ln for ln in pl[-1].read_text().splitlines() if "load.failed" in ln]
        for f in fails:
            print("  powers.log:", f[:200])


# ------------------------------------------------------------------ CLI

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["scan", "port", "unport"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path, help="generate a Power repo here without installing it")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--scope", choices=["user", "project", "all"], default="all",
                    help="CC install-scope filter. Default: all user installs + whatever project scope matches the current directory")
    ap.add_argument("--project-local", action="store_true",
                    help="unpack into the current directory's .kiro/ instead of a Power (matches CC's project scope)")
    ap.add_argument("--from", dest="src", metavar="SOURCE",
                    help="skip install records and go straight to a marketplace/plugin repo: local path | owner/repo | git URL")
    ap.add_argument("--as", dest="mode", choices=["auto", "skills", "power"], default="auto",
                    help="install mode. auto (default): skills-only goes to ~/.kiro/skills/, mixed goes to a Power")
    ap.add_argument("--slash", action="store_true",
                    help="also expose a Power's skills as /name slash commands (symlinks under ~/.kiro/skills/)")
    a = ap.parse_args()
    cwd = Path.cwd().resolve()

    if a.cmd == "unport":
        if not a.names:
            sys.exit("unport: name at least one plugin")
        ok = [unregister(n, cwd) for n in a.names]
        sys.exit(0 if all(ok) else 1)

    if a.src:
        root = fetch_source(a.src)
        # scan just lists (remote entries aren't fetched); port fetches only the ones named
        only = None if a.all else {n.partition("@")[0] for n in a.names}
        cands = marketplace_plugins(root, fetch=(a.cmd == "port"), only=only)
        sources = [{"tool": "market", "name": c["name"], "market": Path(a.src).name, "path": c["path"],
                    "version": c["version"], "scope": "user", "project": "",
                    "remote": c.get("remote")} for c in cands]
        if not sources:
            sys.exit(f"{a.src}: no .claude-plugin/marketplace.json or plugin.json found")
    else:
        sources = scan_sources()
    if a.cmd == "scan":
        LABEL = {"skills": "skills folder copy", "power": "Power", "steering": "manual steering placement"}
        print(f"{'plugin':30} {'source':7} {'components':34} recommendation")
        for s in sources:
            if not s.get("path"):
                r = s.get("remote") or {}
                print(f"{s['name']:30} {s['market'][:7]:7} remote: {r.get('url') or r.get('repo','')}")
                continue
            kind, n = classify(Path(s["path"]))
            rec = recommend(kind)
            comp = " ".join(f"{k[:4]}{v}" for k, v in n.items() if v)
            proj = "·project" if s["scope"] == "project" else ""
            print(f"{s['name']:30} {s['tool']:7} {comp:34} {LABEL[rec]}{proj}")
        print(f"\n{global_skill_count()} global skill(s) (~/.kiro/skills)")
        print("A Power is the default when commands/agents/hooks/mcp are present; skills-only defaults to a folder copy. "
              "Override with `--as skills|power`.")
        return

    def in_scope(s: dict) -> bool:
        # project scope only applies when running inside that project (matches CC's own visibility)
        if s["scope"] == "project":
            here = s.get("project") and cwd.is_relative_to(Path(s["project"]).resolve())
            return a.scope in ("project", "all") and bool(here)
        return a.scope in ("user", "all")
    sources = [s for s in sources if in_scope(s)]

    # Selection: name or name@market. If the same name shows up in several places, prefer CC, newest first
    picked = []
    for key in (["*"] if a.all else a.names):
        n, _, mk = key.partition("@")
        cands = [s for s in sources if (key == "*" or s["name"] == n) and (not mk or s["market"] == mk)]
        unfetched = [s for s in cands if not s.get("path")]
        cands = [s for s in cands if s.get("path")]
        for s in unfetched:
            r = s.get("remote") or {}
            print(f"   ⚠️ {s['name']}: could not fetch the remote repo ({r.get('url') or r.get('repo', '?')})")
        if not cands:
            sys.exit(f"not found: {key} (check with scan)")
        seen = set()
        # If the same name shows up in several places: CC install → Codex install → marketplace source, in that order
        for s in sorted(cands, key=lambda s: (s["tool"] != "claude", s["scope"] == "market", s["name"])):
            if s["name"] not in seen:
                seen.add(s["name"])
                picked.append(s)

    if a.project_local:
        base = cwd / ".kiro/.ported"  # conversion staging area — actual files land in .kiro/{skills,hooks,agents}
    else:
        base = a.out or (KIRO / "powers/installed")
    taken = {p.name for p in (KIRO / "powers/installed").iterdir()} if (KIRO / "powers/installed").is_dir() else set()
    for s in picked:
        name = s["name"]
        if not safe_name(name):
            print(f"\n== {name!r}  → skipped: not a safe plugin name (must match [\\w.-]{{1,64}})")
            continue
        kind, ncomp = classify(Path(s["path"]))
        mode = a.mode if a.mode != "auto" else recommend(kind)
        if a.project_local:
            mode = "project"
        if mode == "steering":
            print(f"\n== {name}  → not moved")
            print(f"   No skills, commands, hooks, or MCP — this reads as guidance rather than a plugin. In Kiro the "
                  f"right place for it is `~/.kiro/steering/` with an `inclusion: always` frontmatter line; "
                  f"wrapping it in a Power would turn 'always applies' into 'keyword-conditional' and change what was intended.\n"
                  f"   Source: {s['path']}")
            continue
        if mode == "skills":
            # Hooks and agents install outside the Power either way, so they're kept even in skills mode.
            lost = [k for k in ("commands", "mcp") if ncomp[k]]
            if lost:  # the user forced --as skills
                print(f"   ⚠️ `{name}` also has {lost}. A skills-only copy won't bring that over — "
                      f"use `--as power` to bring everything.")
        # Power names must be unique across Kiro — a clash silently drops the whole Power list from loading
        if name in taken and not (KIRO / "powers/installed" / name / "PORT-REPORT.md").exists():
            alt = f"{name}-{s['market']}".replace("@", "-")[:64]
            print(f"   ⚠️ `{name}` is already used by another Power → installing as `{alt}` instead")
            name = alt
        taken.add(name)
        power_dir = base / name
        if not power_dir.resolve().is_relative_to(base.resolve()):
            print(f"   ⛔ `{power_dir}` resolves outside `{base}`. Skipped.")
            continue
        where = (f"{cwd}/.kiro/" if mode == "project" else
                 f"{KIRO}/skills/" if mode == "skills" else power_dir)
        why = {"skills": "skills-only, not wrapped in a Power", "power": f"{kind} components", "project": "project-local"}[mode]
        print(f"\n== {name}  ({s['tool']} · {s['market']} · {s['scope']})  →  {where}  [{why}]")
        if a.dry_run:
            continue
        if power_dir.exists():
            if not (power_dir / "PORT-REPORT.md").exists():
                print(f"   ⛔ `{power_dir}` isn't a Power this tool created (an existing Power). Skipped. "
                      f"Remove it from Kiro first if you want to replace it.")
                continue
            shutil.rmtree(power_dir)
        report = [f"# Port report — {name}", f"source: `{s['path']}` ({s['tool']} marketplace `{s['market']}`, scope {s['scope']})", ""]
        Port(s["path"], power_dir, report).run(name, s["version"])
        if mode == "project":
            install_project_local(power_dir, cwd, report, name)
            shutil.rmtree(power_dir)  # staging only — the real files are in .kiro/{skills,hooks,agents}
            if base.is_dir() and not any(base.iterdir()):
                base.rmdir()
        elif mode == "skills" and not a.out:
            install_skills_only(power_dir, report, name)
            shutil.rmtree(power_dir)  # no staging leftovers kept
        elif not a.out:
            if s["scope"] == "project":
                report.append(f"- ⚠️ Source was project-scoped to `{s['project']}` but the Power installed globally. "
                              f"Run `--project-local` from that directory to keep it project-only")
            register(name, power_dir, report, slash=a.slash)
        report.append("")
        report.append("## Worth checking by hand")
        if mode == "skills":
            lost = [k for k in ("commands", "mcp") if ncomp[k]]
            if lost:
                report.append(f"- ❌ {lost} weren't moved — only a Power can carry those (`--as power`)")
            report += [f"- Skills are in `{KIRO}/skills/`. Anything with a name clash was skipped — check the ⚠️ above",
                       "- Run `--as power` again if you need keywords-based auto-activation"]
        else:
            report += ["- `plugin.json`'s `keywords` — pulled mechanically from names. Tune them to the actual phrases that should activate this Power",
                       "- If there were hooks, check they copied to `~/.kiro/hooks/` and that the tool-name mapping is right",
                       "- Agents are plain markdown under `~/.kiro/agents/`. Tool restrictions (`tools`) are stripped"]
        if power_dir.exists():
            (power_dir / "PORT-REPORT.md").write_text("\n".join(report) + "\n")
        else:  # skills and project modes leave no Power directory, so the report is kept separately
            rp = (cwd / ".kiro" if mode == "project" else KIRO) / ".kiro-port-reports" / f"{name}.md"
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text("\n".join(report) + "\n")
        print("\n".join(report[3:]))
    if not a.dry_run and not a.out and not a.project_local:
        reg = [p["name"] for p in _load(KIRO / "powers/installed.json", {"installedPowers": []})["installedPowers"]]
        nskill = sum(1 for n in reg for _ in (KIRO / "powers/installed" / n / "skills").glob("*/SKILL.md"))
        nfile = sum(1 for n in reg for p in (KIRO / "powers/installed" / n).rglob("*") if p.is_file())
        print(f"\n{len(reg)} Power(s) registered · {nskill} skill(s) · {nfile} file(s)")
        if nfile > FILE_WARN * 2:
            print(f"⚠️  {nfile} files. Kiro keeps a file descriptor open per file under a skill tree "
                  f"(kirodotdev/Kiro#10625), which can stall the shell with `spawn EBADF` at this scale.")
    if a.smoke and not a.dry_run and not a.out:
        print("\n== smoke test (kiro-cli --v3)")
        smoke([s["name"] for s in picked])


if __name__ == "__main__":
    main()
