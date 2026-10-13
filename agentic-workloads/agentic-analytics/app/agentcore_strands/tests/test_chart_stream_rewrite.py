"""Unit tests for the agent's outbound chart-tag presigning + stream buffering.

These cover the Step-4b chart contract WITHOUT a live stack or AWS calls:
- a `<chart s3key="...">` tag becomes `<chart url="...">` on the outbound stream,
- the short `s3key` never leaks into the rewritten output (so Memory keeps only the key),
- a `<chart>` tag split across streaming delta boundaries is reassembled, not corrupted.

The agent module imports heavy deps (strands, bedrock_agentcore) that may be absent in
a bare test env, so we load just the two pure helpers from the source file via a tiny
import shim and stub presigning. If the source ever stops exposing them, the test fails
loudly rather than silently passing.
"""

import os
import re
import types
import importlib.util


def _load_agent_helpers():
    """Import _rewrite_chart_tags / _split-equivalent from the agent source.

    We exec only the helper definitions by importing the module with the heavy
    third-party deps stubbed, so the test runs anywhere.
    """
    here = os.path.dirname(__file__)
    src = os.path.abspath(os.path.join(here, "..", "unicorn_rental_agent.py"))

    # Stub modules the agent imports at top level but that we don't need here.
    stub_names = [
        "dotenv", "strands", "strands.models", "strands.tools",
        "strands.tools.mcp", "strands.tools.mcp.mcp_client", "strands.hooks",
        "strands_tools", "strands_tools.code_interpreter", "mcp",
        "mcp.client", "mcp.client.streamable_http", "bedrock_agentcore",
        "bedrock_agentcore.runtime", "bedrock_agentcore.memory",
    ]
    import sys

    class _Any:
        """Accepts any constructor args / call args; usable as base class or callable."""
        def __init__(self, *a, **k):
            pass
        def __call__(self, *a, **k):
            return self
        def entrypoint(self, fn=None, *a, **k):  # decorator: @app.entrypoint
            return fn if fn is not None else (lambda f: f)
        def run(self, *a, **k):
            pass

    def _any_callable(*a, **k):
        return None

    saved = {n: sys.modules.get(n) for n in stub_names}
    for n in stub_names:
        mod = types.ModuleType(n)
        # Symbols the agent imports by name. Capitalised → class-like (instantiable
        # with any args); lowercase → plain callable.
        for attr in ("Agent", "tool", "BedrockModel", "MCPClient", "HookProvider",
                     "HookRegistry", "AgentInitializedEvent", "MessageAddedEvent",
                     "AgentCoreCodeInterpreter", "streamablehttp_client",
                     "BedrockAgentCoreApp", "MemoryClient", "load_dotenv"):
            setattr(mod, attr, _Any if attr[0].isupper() else _any_callable)
        sys.modules[n] = mod

    try:
        spec = importlib.util.spec_from_file_location("_agent_under_test", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        return mod
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


def _stream(mod, deltas):
    """Replay the agent's outbound buffering: feed deltas, return concatenated output."""
    pending = ""
    out = []
    # Mirror the loop in agent_invocation (kept in sync with the source).
    def split_flushable(buf):
        lt = buf.rfind("<")
        if lt == -1:
            return buf, ""
        tail = buf[lt:]
        if ">" in tail:
            return buf, ""
        if len(tail) <= len("<chart") and not "<chart".startswith(tail.lower()):
            return buf, ""
        if len(tail) > mod._CHART_TAG_MAX:
            return buf, ""
        return buf[:lt], tail

    for d in deltas:
        pending += d
        flush, pending = split_flushable(pending)
        if flush:
            out.append(mod._rewrite_chart_tags(flush))
    if pending:
        out.append(mod._rewrite_chart_tags(pending))
    return "".join(out)


def _patch_presign(mod):
    mod._presign_chart_key = lambda s3key: (f"https://signed.example/{s3key}?sig=abc" if s3key else None)


def test_simple_tag_rewritten():
    mod = _load_agent_helpers(); _patch_presign(mod)
    r = _stream(mod, ['Chart: <chart caption="Bookings" s3key="charts/abc.png" /> done'])
    assert 'url="https://signed.example/charts/abc.png' in r
    assert "s3key" not in r          # key must not leak to the consumer
    assert 'caption="Bookings"' in r  # other attrs preserved


def test_tag_split_char_by_char():
    mod = _load_agent_helpers(); _patch_presign(mod)
    text = 'Top breed leads. <chart caption="By breed" s3key="charts/xyz.png" /> See table.'
    r = _stream(mod, list(text))  # one character per delta = worst case
    assert 'url="https://signed.example/charts/xyz.png' in r
    assert "s3key" not in r
    assert r.startswith("Top breed leads. ") and r.rstrip().endswith("See table.")


def test_plain_text_with_bare_lessthan():
    mod = _load_agent_helpers(); _patch_presign(mod)
    r = _stream(mod, ["Just ", "a plain ", "answer with < less-than, no tag."])
    assert r == "Just a plain answer with < less-than, no tag."


def test_two_charts():
    mod = _load_agent_helpers(); _patch_presign(mod)
    r = _stream(mod, ['a <chart s3key="charts/1.png"/> b <chart s3key="charts/2.png"/> c'])
    assert r.count('url="https://signed.example/charts/') == 2
    assert "s3key" not in r


def test_tag_without_s3key_untouched():
    mod = _load_agent_helpers(); _patch_presign(mod)
    r = _stream(mod, ['x <chart caption="nope" /> y'])
    assert '<chart caption="nope" />' in r


if __name__ == "__main__":
    # Allow running directly: python3 test_chart_stream_rewrite.py
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name}: PASS")
    print("\nAll chart-stream-rewrite tests passed.")
