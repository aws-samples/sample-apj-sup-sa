"""Unit tests for the parts of the agent that don't need Blender or Bedrock."""

import pytest

from script_guard import ScriptRejected, check_script


def extract_script(text):
    # Imported lazily so the test doesn't need bedrock_agentcore installed.
    import re
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    return max(blocks, key=len).strip()


def test_allows_normal_modelling_script():
    check_script("import bpy\nimport bmesh\nfrom mathutils import Vector\nimport math\nbpy.ops.mesh.primitive_cube_add(size=math.pi)\n")


@pytest.mark.parametrize("source", [
    "import os",
    "import subprocess",
    "from urllib import request",
    "import bpy\nbpy.utils.execfile('x')",
    "__import__('os')",
    "eval('1')",
    "open('/etc/passwd')",
    "x = ().__class__.__mro__",
    "import bpy\nbpy.ops.wm.save_as_mainfile(filepath='/tmp/x.blend')",
    "def broken(:\n  pass",
])
def test_rejects_escapes_and_bad_syntax(source):
    with pytest.raises(ScriptRejected):
        check_script(source)


def test_empty_reply_explains_the_reasoning_budget():
    # Mirrors app.extract_script's message without importing bedrock_agentcore.
    import ast
    import pathlib
    src = pathlib.Path(__file__).with_name("app.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "extract_script")
    ns = {"ScriptRejected": ScriptRejected, "CODE_BLOCK": __import__("re").compile(r"```(?:python|py)?\s*\n(.*?)```", 16)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "app.py", "exec"), ns)
    with pytest.raises(ScriptRejected, match="output budget on reasoning"):
        ns["extract_script"]("   ")


def test_mantle_path_allowlist_is_in_the_client():
    # Read the constant without importing llm.py (which builds a boto3 client at import time).
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(__file__).with_name("llm.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "MANTLE_PATHS")
    assert ast.literal_eval(node.value) == {"/v1", "/openai/v1"}


def test_extracts_longest_code_block():
    text = "Here:\n```python\nimport bpy\nprint(1)\n```\nand\n```\nx\n```"
    assert extract_script(text) == "import bpy\nprint(1)"


def test_runner_keeps_only_well_formed_metrics():
    import json
    from blender_runner import METRIC_COUNTS, _parse_metrics
    good = {k: 1 for k in METRIC_COUNTS} | {"watertight": True, "extra": "x"}
    out = _parse_metrics(json.dumps(good).encode())
    assert out["watertight"] is True and "extra" not in out and set(METRIC_COUNTS) <= set(out)
    assert _parse_metrics(json.dumps({**good, "holes": -1}).encode()) is None   # out-of-range count
    assert _parse_metrics(json.dumps({**good, "faces": "9"}).encode()) is None  # wrong type
    assert _parse_metrics(b"not json") is None
    assert _parse_metrics(b"\xff\xfe") is None
    assert _parse_metrics(None) is None


def test_sandbox_output_must_be_a_plain_file_owned_by_the_sandbox(tmp_path):
    import os
    from blender_runner import BlenderError, _read_sandbox_file
    me = os.getuid()
    secret = tmp_path / "secret"
    secret.write_bytes(b"AWS_SECRET")
    work = tmp_path / "work"
    work.mkdir()

    ok = work / "model.glb"
    ok.write_bytes(b"glTF")
    assert _read_sandbox_file(ok, me, 100) == b"glTF"
    assert _read_sandbox_file(work / "missing.glb", me, 100) is None

    with pytest.raises(BlenderError, match="written by the script"):
        _read_sandbox_file(ok, me + 1, 100)        # not owned by the sandbox user
    with pytest.raises(BlenderError, match="under"):
        _read_sandbox_file(ok, me, 2)              # too large

    link = work / "symlink.glb"
    link.symlink_to(secret)
    with pytest.raises(BlenderError, match="not a regular file"):
        _read_sandbox_file(link, me, 100)

    hard = work / "hardlink.glb"
    os.link(secret, hard)
    with pytest.raises(BlenderError, match="written by the script"):
        _read_sandbox_file(hard, me, 100)          # two links: points at a file outside the work folder

    fifo = work / "fifo.glb"
    os.mkfifo(fifo)
    with pytest.raises(BlenderError, match="written by the script"):
        _read_sandbox_file(fifo, me, 100)


def _fake_blender(tmp_path, monkeypatch, body):
    """Put a fake `blender` first on PATH; it gets the runner's argv: ... -- script out metrics."""
    import os
    import blender_runner
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "blender"
    exe.write_text("#!/bin/sh\nfor a; do :; done\nmetrics=$a\nout=$(dirname \"$metrics\")/model.glb\n" + body)
    exe.chmod(0o755)
    monkeypatch.setattr(blender_runner.subprocess, "Popen", _with_path(blender_runner.subprocess.Popen, str(bindir)))
    return blender_runner


def _with_path(popen, bindir):
    def wrapped(cmd, **kw):
        kw["env"] = {**kw["env"], "PATH": bindir + ":" + kw["env"]["PATH"]}
        return popen(cmd, **kw)
    return wrapped


def test_run_script_returns_the_glb(tmp_path, monkeypatch):
    br = _fake_blender(tmp_path, monkeypatch, 'printf glTF > "$out"\n')
    assert br.run_script("import bpy").glb == b"glTF"


def test_run_script_rejects_a_symlinked_glb(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_bytes(b"AWS_SECRET")
    br = _fake_blender(tmp_path, monkeypatch, f'ln -s {secret} "$out"\n')
    with pytest.raises(br.BlenderError, match="not a regular file"):
        br.run_script("import bpy")


def test_run_script_kills_processes_the_script_leaves_behind(tmp_path, monkeypatch):
    import time
    marker = tmp_path / "late"
    br = _fake_blender(tmp_path, monkeypatch, f'printf glTF > "$out"\n(sleep 1; touch {marker}) &\n')
    assert br.run_script("import bpy").glb == b"glTF"
    time.sleep(1.5)
    assert not marker.exists()


def test_image_goes_before_the_text_for_both_apis(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import llm
    convo = llm.Conversation(system="s", messages=[
        {"role": "user", "text": "make this", "image": {"format": "png", "bytes": b"\x89PNG"}},
        {"role": "assistant", "text": "ok"},
    ])
    first = llm.converse_messages(convo)[0]["content"]
    assert first[0] == {"image": {"format": "png", "source": {"bytes": b"\x89PNG"}}} and first[1] == {"text": "make this"}
    mantle = llm.mantle_messages(convo)
    assert mantle[0]["role"] == "system" and mantle[2] == {"role": "assistant", "content": "ok"}
    assert mantle[1]["content"][0]["image_url"]["url"] == "data:image/png;base64,iVBORw=="


def test_blender_drops_privileges_through_popen_arguments_not_preexec_fn(tmp_path, monkeypatch):
    # preexec_fn is unsafe in a threaded server; Popen's user/group/extra_groups are applied safely in the child.
    import pwd
    import blender_runner as br
    sandbox = pwd.struct_passwd(("sandbox", "x", 1234, 5678, "", str(tmp_path), "/usr/sbin/nologin"))
    monkeypatch.setattr(br.os, "getuid", lambda: 0)
    monkeypatch.setattr(br.pwd, "getpwnam", lambda name: sandbox)
    monkeypatch.setattr(br.os, "chown", lambda *a: None)
    seen = {}

    def fake_popen(cmd, **kw):
        seen.update(kw)
        raise RuntimeError("stop before running blender")

    monkeypatch.setattr(br.subprocess, "Popen", fake_popen)
    with pytest.raises(RuntimeError, match="stop before"):
        br.run_script("import bpy")
    assert "preexec_fn" not in seen
    assert seen["user"] == 1234 and seen["group"] == 5678 and seen["extra_groups"] == []
    assert seen["start_new_session"] is True


def test_no_privilege_arguments_when_not_root():
    import blender_runner as br
    assert br._drop_privileges(None) == {}
