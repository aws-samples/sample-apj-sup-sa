"""Regression tests for shell-argument splitting in batch runtime/run.sh.

The batch container entrypoint receives plan-author-provided
``EXTRA_SERVE_FLAGS`` as a single env-var string and must re-tokenize it
into argv elements that survive bash quoting. The bug fixed here was:
unquoted ``${EXTRA_SERVE_FLAGS}`` expansion only word-splits on $IFS and
does NOT honor embedded single quotes, so a value like
``--limit-mm-per-prompt '{"image":4}'`` would reach vLLM as the literal
token ``'{"image":4}'`` (with quote chars) and json.loads() would reject
it. The fix uses ``eval ARR=(${VAR})`` to re-parse with full bash quoting,
then expands ``"${ARR[@]}"`` in the vllm serve invocation.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

RUN_SH = (
    Path(__file__).resolve().parents[1]
    / "src" / "llm_batch_deploy" / "runtime" / "run.sh"
)


def _split(extra: str) -> list[str]:
    """Replicate run.sh's tokenization of EXTRA_SERVE_FLAGS via bash.

    The ``${ARR[@]+"${ARR[@]}"}`` idiom keeps the test portable to bash 3.2
    (macOS default), where ``"${ARR[@]}"`` on an empty array trips
    ``set -u``. The runtime container ships bash 5+, so run.sh itself uses
    the simpler form.
    """
    script = (
        "set -euo pipefail\n"
        "declare -a EXTRA_SERVE_FLAGS_ARR=()\n"
        "if [[ -n \"${EXTRA_SERVE_FLAGS}\" ]]; then\n"
        "  eval \"EXTRA_SERVE_FLAGS_ARR=(${EXTRA_SERVE_FLAGS})\"\n"
        "fi\n"
        "for a in ${EXTRA_SERVE_FLAGS_ARR[@]+\"${EXTRA_SERVE_FLAGS_ARR[@]}\"}; "
        "do printf '%s\\n' \"$a\"; done\n"
    )
    # Static script string, fixed argv. shell=False (default for argv list).
    # The test inputs are author-controlled in this test module; there is
    # no untrusted-input surface here.
    out = subprocess.check_output(  # nosec B603
        ["bash", "-c", script],
        env={"EXTRA_SERVE_FLAGS": extra},
        text=True,
    )
    return [line for line in out.splitlines() if line]


def test_simple_flags_split_on_whitespace() -> None:
    assert _split("--kv-cache-dtype fp8") == ["--kv-cache-dtype", "fp8"]


def test_json_value_with_single_quotes_survives() -> None:
    """The Qwen3-VL bug: a single-quoted JSON object should reach vLLM as one
    argument with the quote characters stripped (so json.loads() can parse it).
    """
    extra = "--limit-mm-per-prompt '{\"image\":4}' --max-num-seqs 16"
    parts = _split(extra)
    assert parts == [
        "--limit-mm-per-prompt",
        '{"image":4}',
        "--max-num-seqs",
        "16",
    ]


def test_double_quoted_value_survives() -> None:
    extra = '--served-model-name "qwen3-vl"'
    assert _split(extra) == ["--served-model-name", "qwen3-vl"]


def test_empty_extra_serve_flags_yields_no_args() -> None:
    assert _split("") == []


def test_run_sh_uses_array_expansion() -> None:
    """Pin the actual run.sh text — guards against accidental revert.

    If someone re-introduces unquoted ``${EXTRA_SERVE_FLAGS}`` in the
    vllm serve invocation, this fails fast with a meaningful diff.
    """
    text = RUN_SH.read_text()
    assert 'EXTRA_SERVE_FLAGS_ARR=()' in text, (
        "run.sh must declare EXTRA_SERVE_FLAGS_ARR before vllm serve "
        "(this fixes JSON-valued flag tokenization)."
    )
    assert '"${EXTRA_SERVE_FLAGS_ARR[@]}"' in text, (
        "run.sh must expand the array with quoting in vllm serve."
    )
    # Make sure the OLD pattern (unquoted ${EXTRA_SERVE_FLAGS} as final
    # argv) is gone — that's the bug we just fixed.
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == "${EXTRA_SERVE_FLAGS} \\":
            raise AssertionError(
                f"run.sh:{i+1} still uses unquoted ${{EXTRA_SERVE_FLAGS}} "
                "expansion — the JSON-valued flag bug will return."
            )


def test_python_argv_after_run_sh_round_trip() -> None:
    """End-to-end: simulate what argv vLLM's argparse will see.

    The smoke test for Qwen3-VL had `--limit-mm-per-prompt` followed by
    a JSON object as a single arg. Confirm Python (which parses argv as
    char-list) sees what vLLM expects.
    """
    extra = "--limit-mm-per-prompt '{\"image\":4}'"
    args = _split(extra)
    # Re-quote with shlex.join, then re-split with shlex.split — proves the
    # tokens are well-formed Python argv (no embedded shell metacharacters).
    requoted = shlex.join(args)
    assert shlex.split(requoted) == args
    # The actual JSON value must round-trip through json.loads.
    import json
    idx = args.index("--limit-mm-per-prompt")
    assert json.loads(args[idx + 1]) == {"image": 4}
