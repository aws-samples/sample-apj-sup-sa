# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

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


def test_runner_keeps_only_well_formed_metrics(tmp_path):
    import json
    from blender_runner import METRIC_COUNTS, _read_metrics
    good = {k: 1 for k in METRIC_COUNTS} | {"watertight": True, "extra": "x"}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(good))
    out = _read_metrics(p)
    assert out["watertight"] is True and "extra" not in out and set(METRIC_COUNTS) <= set(out)
    p.write_text(json.dumps({**good, "holes": -1}))
    assert _read_metrics(p) is None          # out-of-range count
    p.write_text(json.dumps({**good, "faces": "9"}))
    assert _read_metrics(p) is None          # wrong type
    p.write_text("not json")
    assert _read_metrics(p) is None
    assert _read_metrics(tmp_path / "missing.json") is None
