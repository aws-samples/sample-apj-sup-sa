"""Static checks on model-generated Blender scripts before they run.

Defence in depth only: the script also runs as an unprivileged user with an empty
environment and a timeout. This screen rejects the obvious ways out of a modelling
script (imports beyond bpy and maths, file and process access, dunder tricks).
"""

import ast

ALLOWED_IMPORTS = {"bpy", "bmesh", "mathutils", "math", "random", "colorsys", "itertools", "functools"}
BLOCKED_NAMES = {
    "__import__", "eval", "exec", "compile", "open", "input", "breakpoint",
    "globals", "locals", "vars", "memoryview", "help",
}
BLOCKED_ATTRIBUTES = {
    "execfile", "system", "popen", "spawn", "driver_namespace", "script_paths",
    "user_resource", "load_post", "save_post", "read_homefile", "open_mainfile",
    "save_mainfile", "save_as_mainfile", "addon_install", "script", "run_script",
    "python_file_run", "as_module",
}
MAX_SCRIPT_CHARS = 60_000


class ScriptRejected(ValueError):
    pass


def check_script(source: str) -> None:
    if len(source) > MAX_SCRIPT_CHARS:
        raise ScriptRejected(f"Script is {len(source)} characters; the limit is {MAX_SCRIPT_CHARS}.")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ScriptRejected(f"SyntaxError: {exc.msg} (line {exc.lineno})") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_module(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise ScriptRejected("Relative imports are not allowed.")
            _check_module(node.module or "")
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise ScriptRejected(f"'{node.id}' is not allowed (line {node.lineno}).")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in BLOCKED_ATTRIBUTES:
                raise ScriptRejected(f"Attribute '{node.attr}' is not allowed (line {node.lineno}).")


def _check_module(name: str) -> None:
    root = name.split(".")[0]
    if root not in ALLOWED_IMPORTS:
        allowed = ", ".join(sorted(ALLOWED_IMPORTS))
        raise ScriptRejected(f"Import of '{name}' is not allowed. Only these modules may be imported: {allowed}.")
