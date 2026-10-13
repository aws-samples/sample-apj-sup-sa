#!/usr/bin/env python3
"""Regression tests for kiro-port.py. Ports typed fixtures under an isolated KIRO_HOME and checks the result.

    python3 tests/regression-test.py            # everything
    python3 tests/regression-test.py -k hook    # only tests with "hook" in the name

No framework, no network. Doesn't launch a real Kiro — the script honours KIRO_HOME, so everything
lands in a temp directory that is deleted afterwards.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
PORT = HERE.parent / "skills/import-plugins/scripts/kiro-port.py"
FAIL: list[str] = []
PASS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))


# ------------------------------------------------------------------ fixtures

def fx_skills_only(root: Path) -> Path:
    """Skills-only plugin -> should land globally."""
    d = root / "fx-skills"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin/plugin.json").write_text(json.dumps(
        {"name": "fx-skills", "version": "2.1.0", "description": "skills only"}) + "\n")
    for n in ("alpha", "beta"):
        (d / "skills" / n).mkdir(parents=True)
        (d / "skills" / n / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: Fixture skill {n}.\n---\n\nBody of {n}. Keep me byte-identical.\n")
    return d


def fx_commands(root: Path) -> Path:
    """Plugin with a command -> a Power. allowed-tools/argument-hint should be dropped."""
    d = root / "fx-cmd"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fx-cmd", "description": "cmd"}) + "\n")
    (d / "commands").mkdir()
    (d / "commands/deploy.md").write_text(
        "---\nallowed-tools: Bash(git *)\nargument-hint: \"[env]\"\ndescription: Deploy it\n---\n\n"
        "Run the deploy. Body must not change.\n")
    (d / "commands/skip.toml").write_text("# not markdown\n")
    return d


def fx_hooks(root: Path) -> Path:
    """Conditional hooks — if/timeout should be reported, and names should stay distinct."""
    d = root / "fx-hook"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fx-hook", "description": "hooks"}) + "\n")
    (d / "hooks").mkdir()
    (d / "hooks/run.sh").write_text("#!/bin/sh\necho hi\n")
    (d / "hooks/run.cmd").write_text("echo cmd-ok\n")
    (d / "hooks/hooks.json").write_text(json.dumps({"hooks": {
        "PostToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh"', "if": "Bash(git commit:*)"},
            {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh"', "if": "Bash(git push:*)"}]}],
        "PreToolUse": [{"matcher": "Write|Edit", "hooks": [
            {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh"', "timeout": 10}]}],
        "SessionStart": [{"hooks": [
            {"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/run.cmd" go', "shell": "bash"}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": "true"}]}],
    }}) + "\n")
    (d / "skills/only").mkdir(parents=True)
    (d / "skills/only/SKILL.md").write_text("---\nname: only\ndescription: Fixture.\n---\n\nBody.\n")
    return d


def fx_flat_hooks(root: Path) -> Path:
    """hooks.json written as a flat list of {event, command} objects — the other schema in the wild."""
    d = root / "fx-flathook"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fx-flathook", "description": "flat"}) + "\n")
    (d / "hooks").mkdir()
    (d / "hooks/hooks.json").write_text(json.dumps({"hooks": [
        {"event": "Stop", "command": "echo stop"},
        {"event": "UserPromptSubmit", "command": "echo prompt"}]}) + "\n")
    return d


def fx_mcp(root: Path) -> Path:
    """One stdio server plus one remote http server — the remote one should be flagged."""
    d = root / "fx-mcp"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fx-mcp", "description": "mcp"}) + "\n")
    (d / ".mcp.json").write_text(json.dumps({
        "local": {"type": "stdio", "command": "uvx", "args": ["x"]},
        "remote": {"type": "http", "url": "https://example.invalid/mcp"}}) + "\n")
    return d


def fx_agents(root: Path) -> Path:
    """Agent — tools/model should be dropped, body kept."""
    d = root / "fx-agent"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fx-agent", "description": "agents"}) + "\n")
    (d / "agents").mkdir()
    (d / "agents/rev.md").write_text(
        "---\nname: rev\ndescription: Reviewer.\ntools: Read, Grep\nmodel: opus\n---\n\nYou are rev. Keep body.\n")
    (d / "skills/s1").mkdir(parents=True)
    (d / "skills/s1/SKILL.md").write_text("---\nname: s1\ndescription: Fixture.\n---\n\nBody.\n")
    return d


def fx_bulk(root: Path) -> Path:
    """Plugin with a large excluded tree (node_modules) — should not get copied."""
    d = root / "fx-bulk"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fx-bulk", "description": "bulk"}) + "\n")
    (d / "skills/s1").mkdir(parents=True)
    (d / "skills/s1/SKILL.md").write_text("---\nname: s1\ndescription: Fixture.\n---\n\nBody.\n")
    for i in range(50):
        (d / "node_modules" / f"pkg{i}").mkdir(parents=True)
        (d / "node_modules" / f"pkg{i}" / "index.js").write_text("//\n")
    return d


def fx_guidance(root: Path) -> Path:
    """Guidance only — should not be moved, just pointed at steering."""
    d = root / "fx-guide"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fx-guide", "description": "guide"}) + "\n")
    (d / "CLAUDE.md").write_text("# guidance\nAlways be nice.\n")
    return d


def fx_agent_plugin(root: Path) -> Path:
    """Already an Agent Plugin (root plugin.json with $schema) — the author's keywords must survive."""
    d = root / "fx-ap"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "fx-ap", "version": "3.0.0", "description": "already agent plugins",
        "keywords": ["fx-ap", "플러그인 가져와", "bring plugins", "custom phrase"]}) + "\n")
    (d / "skills/s1").mkdir(parents=True)
    (d / "skills/s1/SKILL.md").write_text("---\nname: s1\ndescription: Fixture.\n---\n\nBody.\n")
    return d


def fx_marketplace(root: Path, plugins: list[Path], entries: list[dict] | None = None) -> Path:
    """A local marketplace holding the fixtures. `entries` overrides the generated plugin list."""
    mk = root / "market"
    (mk / ".claude-plugin").mkdir(parents=True)
    for p in plugins:
        shutil.copytree(p, mk / p.name)
    if entries is None:
        entries = [{"name": p.name, "source": f"./{p.name}", "description": p.name} for p in plugins]
    (mk / ".claude-plugin/marketplace.json").write_text(json.dumps({
        "name": "fx", "owner": {"name": "t"}, "plugins": entries}) + "\n")
    return mk


# ------------------------------------------------------------------ run helper

def run(kiro_home: Path, *args: str, cwd: Path | None = None) -> tuple[int, str]:
    env = dict(os.environ, KIRO_HOME=str(kiro_home))
    r = subprocess.run([sys.executable, str(PORT), *args], capture_output=True, text=True, env=env,
                       timeout=300, cwd=str(cwd) if cwd else None)
    return r.returncode, r.stdout + r.stderr


def same_body(a: Path, b: Path) -> bool:
    def body(p: Path) -> str:
        t = p.read_text()
        if t.startswith("---"):
            e = t.find("\n---", 3)
            return t[e + 4:] if e > 0 else t
        return t
    return body(a) == body(b)


# ------------------------------------------------------------------ tests

def test_skills_only(root, kh, mk):
    print("\n[skills-only] a skills-only plugin goes globally, no Power gets built")
    rc, out = run(kh, "port", "--from", str(mk), "fx-skills")
    check("exit code 0", rc == 0, out[-400:])
    check("no Power created", not (kh / "powers/installed/fx-skills").exists())
    check("2 global skills", len(list((kh / "skills").glob("*/SKILL.md"))) >= 2)
    check("body unchanged", same_body(root / "fx-skills/skills/alpha/SKILL.md", kh / "skills/alpha/SKILL.md"))
    man = json.loads((kh / ".kiro-port-manifest.json").read_text())
    check("manifest mode=skills", man.get("fx-skills", {}).get("mode") == "skills")
    rc, _ = run(kh, "unport", "fx-skills")
    check("unport removes the skill", not (kh / "skills/alpha").exists())


def test_commands(root, kh, mk):
    print("\n[commands] commands become skills, allowed-tools/argument-hint dropped, .toml reported")
    rc, out = run(kh, "port", "--from", str(mk), "fx-cmd")
    sk = kh / "powers/installed/fx-cmd/skills/deploy/SKILL.md"
    check("Power created", sk.exists(), out[-400:])
    if sk.exists():
        fm = sk.read_text().split("---")[1]
        check("name added", "name: deploy" in fm)
        check("allowed-tools dropped", "allowed-tools" not in fm)
        check("argument-hint dropped", "argument-hint" not in fm)
        check("body unchanged", same_body(root / "fx-cmd/commands/deploy.md", sk))
    check(".toml reported as unconverted", "toml" in out)
    check("version defaults to 1.0.0 when missing", '"version": "1.0.0"' in (kh / "powers/installed/fx-cmd/plugin.json").read_text())
    run(kh, "unport", "fx-cmd")


def test_hooks(root, kh, mk):
    print("\n[hooks] per-condition hook names stay distinct, lost fields reported, SessionEnd skipped")
    rc, out = run(kh, "port", "--from", str(mk), "fx-hook")
    hf = kh / "hooks/fx-hook.json"
    check("hook file installed", hf.exists(), out[-500:])
    if hf.exists():
        hooks = json.loads(hf.read_text())["hooks"]
        names = [h["name"] for h in hooks]
        check("condition folded into the name", any("git-commit" in n for n in names) and any("git-push" in n for n in names),
              str(names))
        check("no duplicate names", len(names) == len(set(names)))
        matchers = [h.get("matcher") for h in hooks]
        check("tool-name mapping (execute_bash)", "execute_bash" in str(matchers))
        check("Write|Edit → fs_write", "fs_write" in str(matchers))
        ss = [h for h in hooks if h["trigger"] == "SessionStart"]
        check("shell field wrapped as bash -c", bool(ss) and ss[0]["action"]["command"].startswith("bash -c "),
              str(ss[0]["action"]["command"][:60]) if ss else "no SessionStart hook")
        check("shell isn't reported as a loss", "`shell`" not in out)
        check("event names kept as-is", {h["trigger"] for h in hooks} <= {"PreToolUse", "PostToolUse", "SessionStart"})
    check("if field loss reported", "`if`" in out)
    check("timeout loss reported", "timeout" in out)
    check("SessionEnd exclusion reported", "SessionEnd" in out)
    run(kh, "unport", "fx-hook")
    check("unport removes the hook", not hf.exists())


def test_flat_hooks(root, kh, mk):
    print("\n[hooks] flat-list hooks.json is accepted, event names not renamed")
    rc, out = run(kh, "port", "--from", str(mk), "fx-flathook")
    hf = kh / "hooks/fx-flathook.json"
    check("flat list converted", hf.exists(), out[-400:])
    if hf.exists():
        trig = sorted(h["trigger"] for h in json.loads(hf.read_text())["hooks"])
        check("Stop and UserPromptSubmit kept verbatim", trig == ["Stop", "UserPromptSubmit"], str(trig))
    run(kh, "unport", "fx-flathook")


def test_mcp(root, kh, mk):
    print("\n[mcp] server definitions unchanged, remote http flagged")
    rc, out = run(kh, "port", "--from", str(mk), "fx-mcp")
    mj = kh / "powers/installed/fx-mcp/mcp.json"
    check("mcp.json generated", mj.exists(), out[-400:])
    if mj.exists():
        d = json.loads(mj.read_text())
        check("has $schema", d.get("$schema", "").endswith("mcp.schema.json"))
        check("wrapped in mcpServers", set(d.get("mcpServers", {})) == {"local", "remote"})
        check("server definition unchanged", d["mcpServers"]["local"]["command"] == "uvx")
    check("remote MCP warning", "Remote MCP" in out and "remote" in out)
    st = kh / "settings/mcp.json"
    ps = json.loads(st.read_text()).get("powers", {}).get("mcpServers", {}) if st.exists() else {}
    check("stdio server registered as power-<name>-<server>", "power-fx-mcp-local" in ps and ps["power-fx-mcp-local"].get("command") == "uvx", str(ps))
    check("remote server not registered", "power-fx-mcp-remote" not in ps)
    run(kh, "unport", "fx-mcp")
    ps = json.loads(st.read_text()).get("powers", {}).get("mcpServers", {}) if st.exists() else {}
    check("unport removes the MCP registration", "power-fx-mcp-local" not in ps)


def test_agents(root, kh, mk):
    print("\n[agents] body kept, tools/model dropped and reported")
    rc, out = run(kh, "port", "--from", str(mk), "fx-agent")
    ag = kh / "agents/rev.md"
    check("agent installed", ag.exists(), out[-400:])
    if ag.exists():
        fm = ag.read_text().split("---")[1]
        check("tools dropped", "tools:" not in fm)
        check("model dropped", "model:" not in fm)
        check("body unchanged", same_body(root / "fx-agent/agents/rev.md", ag))
    check("dropped fields reported", "tools" in out and "model" in out)
    run(kh, "unport", "fx-agent")
    check("unport removes the agent", not ag.exists())


def test_bulk(root, kh, mk):
    print("\n[bulk] node_modules is not copied (avoids fd exhaustion)")
    rc, out = run(kh, "port", "--from", str(mk), "fx-bulk")
    check("node_modules excluded", not any((kh / "skills").rglob("node_modules")))
    check("exclusion reported", "node_modules" in out)
    run(kh, "unport", "fx-bulk")


def test_guidance(root, kh, mk):
    print("\n[guidance] guidance-only is not moved, just pointed at steering")
    rc, out = run(kh, "port", "--from", str(mk), "fx-guide")
    check("not moved", not (kh / "powers/installed/fx-guide").exists(), out[-300:])
    check("steering path mentioned", "steering" in out)


def test_overrides(root, kh, mk):
    print("\n[--as] the override flag flips the classification")
    run(kh, "port", "--from", str(mk), "fx-skills", "--as", "power")
    check("--as power creates a Power", (kh / "powers/installed/fx-skills").exists())
    run(kh, "unport", "fx-skills")
    run(kh, "port", "--from", str(mk), "fx-cmd", "--as", "skills")
    check("--as skills places it globally", (kh / "skills/deploy").exists())
    rc, out = run(kh, "port", "--from", str(mk), "fx-mcp", "--as", "skills")
    check("--as skills on a mixed plugin warns", "mcp" in out.lower())
    check("--as skills on a plugin with no skills/ doesn't crash", rc == 0 and "Traceback" not in out, out[-400:])
    check("no staging directory left behind", not (kh / "powers/installed/fx-mcp").exists())
    run(kh, "unport", "fx-cmd")
    run(kh, "unport", "fx-mcp")


def test_guards(root, kh, mk):
    print("\n[guards] won't overwrite an existing Power, avoids name clashes")
    (kh / "powers/installed/preexisting").mkdir(parents=True, exist_ok=True)
    (kh / "powers/installed/preexisting/plugin.json").write_text('{"name":"preexisting"}')
    p = root / "preexisting"
    (p / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (p / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "preexisting", "description": "x"}))
    (p / "commands").mkdir(exist_ok=True)
    (p / "commands/c.md").write_text("---\ndescription: c\n---\n\nbody\n")
    mk2 = fx_marketplace(root / "m2", [p])
    rc, out = run(kh, "port", "--from", str(mk2), "preexisting")
    check("existing Power protected", "⛔" in out or "this tool created" in out or (kh / "powers/installed/preexisting-market").exists(),
          out[-300:])


def test_agent_plugin_source(root, kh, mk):
    print("\n[agent-plugin source] a source that is already an Agent Plugin keeps its own plugin.json")
    rc, out = run(kh, "port", "--from", str(root / "fx-ap"), "fx-ap", "--as", "power")
    pj = kh / "powers/installed/fx-ap/plugin.json"
    check("Power created", pj.exists(), out[-400:])
    if pj.exists():
        d = json.loads(pj.read_text())
        check("author's keywords kept", d.get("keywords") == ["fx-ap", "플러그인 가져와", "bring plugins", "custom phrase"], str(d.get("keywords")))
        check("author's version kept", d.get("version") == "3.0.0")
    check("report says manifest was kept", "already an Agent Plugin" in out)
    run(kh, "unport", "fx-ap")


def test_slash(root, kh, mk):
    print("\n[--slash] a Power's skills are exposed as /name via symlinks, removed on unport")
    (kh / "skills/deploy").mkdir(parents=True)  # pre-existing real skill with a clashing name
    (kh / "skills/deploy/SKILL.md").write_text("---\nname: deploy\ndescription: mine\n---\n\nmine\n")
    # a stale link into the same Power path (left by an earlier install) must be taken over, not treated as a clash
    (kh / "skills/only").symlink_to(kh / "powers/installed/fx-hook/skills/only")
    rc, out = run(kh, "port", "--from", str(mk), "fx-hook", "--as", "power", "--slash")
    link = kh / "skills/only"
    check("stale link into the same Power is adopted", "/only" in out and "already exists" not in out.split("fx-hook")[-1][:600], out[-400:])
    check("symlink created", link.is_symlink(), out[-400:])
    check("symlink points into the Power", link.is_symlink() and (kh / "powers/installed/fx-hook/skills/only").resolve() == link.resolve())
    check("report lists /only", "/only" in out)
    rc, out = run(kh, "port", "--from", str(mk), "fx-cmd", "--slash")
    check("clashing name not overwritten", not (kh / "skills/deploy").is_symlink() and "mine" in (kh / "skills/deploy/SKILL.md").read_text())
    check("clash reported", "already exists" in out)
    # slash name comes from SKILL.md `name:`, and built-in collisions are called out
    p = root / "fx-slashnames"
    (p / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (p / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fx-slashnames", "description": "x"}))
    (p / "skills/dirname").mkdir(parents=True, exist_ok=True)
    (p / "skills/dirname/SKILL.md").write_text("---\nname: real-slash-name\ndescription: x\n---\n\nbody\n")
    (p / "skills/help").mkdir(parents=True, exist_ok=True)
    (p / "skills/help/SKILL.md").write_text("---\nname: help\ndescription: x\n---\n\nbody\n")
    rc, out = run(kh, "port", "--from", str(p), "fx-slashnames", "--as", "power", "--slash")
    check("report uses the frontmatter name", "/real-slash-name" in out and "/dirname" not in out, out[-500:])
    check("built-in collision warned", "/help" in out and "built-in" in out, out[-500:])
    run(kh, "unport", "fx-slashnames")
    run(kh, "unport", "fx-hook")
    check("unport removes the symlink", not link.exists() and not link.is_symlink())
    run(kh, "unport", "fx-cmd")
    check("real skill untouched by unport", (kh / "skills/deploy/SKILL.md").exists())
    shutil.rmtree(kh / "skills/deploy")


def test_untrusted_names(root, kh, mk):
    print("\n[untrusted] marketplace entries can't name paths outside ~/.kiro or the repo")
    escape_abs = root / "escaped-abs"          # an absolute path an attacker might target
    outside = root / "outside-repo"            # a plugin dir outside the marketplace repo
    (outside / ".claude-plugin").mkdir(parents=True)
    (outside / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "ok-name", "description": "x"}))
    (outside / "skills/leak").mkdir(parents=True)
    (outside / "skills/leak/SKILL.md").write_text("---\nname: leak\ndescription: x\n---\n\nleaked\n")
    mk3 = fx_marketplace(root / "m3", [root / "fx-skills"], entries=[
        {"name": str(escape_abs), "source": "./fx-skills", "description": "absolute name"},
        {"name": "../escaped-rel", "source": "./fx-skills", "description": "traversal name"},
        {"name": "ok-name", "source": "../outside-repo", "description": "source outside the repo"},
        {"name": "bad name;rm", "source": "./fx-skills", "description": "shell chars"},
    ])
    rc, out = run(kh, "port", "--from", str(mk3), "--all")
    check("absolute-path name not created", not escape_abs.exists())
    check("traversal name not created", not (kh.parent / "escaped-rel").exists() and not (kh / "escaped-rel").exists())
    check("nothing leaked from outside the repo", not (kh / "skills/leak").exists()
          and not (kh / "powers/installed/ok-name").exists())
    check("unsafe names reported", "unsafe name" in out, out[-600:])
    check("outside source reported", "outside the marketplace repo" in out, out[-600:])
    check("nothing installed", not any((kh / "powers/installed").iterdir()) and not list((kh / "skills").glob("*/SKILL.md")))


def test_project_local(root, kh, mk):
    print("\n[--project-local] writes into ./.kiro, records it, and unport removes exactly that")
    proj = root / "proj"
    proj.mkdir()
    rc, out = run(kh, "port", "--from", str(mk), "fx-hook", "--project-local", cwd=proj)
    k = proj / ".kiro"
    check("exit code 0", rc == 0, out[-400:])
    check("skill in project", (k / "skills/only/SKILL.md").exists())
    check("hook in project", (k / "hooks/fx-hook.json").exists())
    check("hook script preserved in project", (k / ".kiro-port-assets/fx-hook/hooks/run.sh").exists())
    if (k / "hooks/fx-hook.json").exists():
        check("hook command points at preserved copy", ".ported" not in (k / "hooks/fx-hook.json").read_text())
    check("staging removed", not (k / ".ported").exists())
    check("nothing written to global KIRO_HOME", not (kh / "hooks/fx-hook.json").exists()
          and not (kh / "skills/only").exists())
    man = k / ".kiro-port-manifest.json"
    check("project manifest written", man.exists() and json.loads(man.read_text()).get("fx-hook", {}).get("mode") == "project")
    rc, out = run(kh, "port", "--from", str(mk), "fx-mcp", "--project-local", cwd=proj)
    mcp = k / "settings/mcp.json"
    check("mcp merged into project settings", mcp.exists() and {"local", "remote"} <= set(json.loads(mcp.read_text())["mcpServers"]),
          out[-300:])
    rc, out = run(kh, "unport", "fx-hook", cwd=proj)
    check("unport exit 0", rc == 0, out)
    check("skill removed", not (k / "skills/only").exists())
    check("hook removed", not (k / "hooks/fx-hook.json").exists())
    check("preserved copy removed", not (k / ".kiro-port-assets/fx-hook").exists())
    rc, out = run(kh, "unport", "fx-mcp", cwd=proj)
    check("mcp keys removed", not json.loads(mcp.read_text())["mcpServers"], mcp.read_text())
    check("project manifest gone when empty", not man.exists())
    rc, out = run(kh, "unport", "fx-hook", cwd=proj)
    check("unport of unknown name exits non-zero", rc != 0, f"rc={rc}")
    check("unport of unknown name says so", "nothing to remove" in out, out[-300:])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", dest="filt", default="")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="kiro-port-regress-"))
    root, kh = tmp / "fixtures", tmp / "kiro-home"
    root.mkdir(); (kh / "skills").mkdir(parents=True); (kh / "powers/installed").mkdir(parents=True)
    (kh / "powers/registries").mkdir(parents=True)
    (kh / "powers/installed.json").write_text('{"version":"1.0.0","installedPowers":[],"dismissedAutoInstalls":[]}')
    (kh / "powers/registries/user-added.json").write_text('{"powers":[],"version":"1.0.0"}')
    plugins = [f(root) for f in (fx_skills_only, fx_commands, fx_hooks, fx_flat_hooks, fx_mcp, fx_agents, fx_bulk, fx_guidance)]
    fx_agent_plugin(root)
    mk = fx_marketplace(root / "m1", plugins)
    tests = [test_skills_only, test_commands, test_hooks, test_flat_hooks, test_mcp, test_agents,
             test_bulk, test_guidance, test_overrides, test_agent_plugin_source, test_slash, test_untrusted_names,
             test_project_local, test_guards]
    print(f"isolated environment: {kh}")
    for t in tests:
        if a.filt and a.filt not in t.__name__:
            continue
        try:
            t(root, kh, mk)
        except Exception as e:  # a test that errors out also counts as a failure
            check(f"{t.__name__} run", False, repr(e))
    print(f"\npassed {len(PASS)} · failed {len(FAIL)}")
    if FAIL:
        print("failures:")
        for f in FAIL:
            print("  -", f)
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
