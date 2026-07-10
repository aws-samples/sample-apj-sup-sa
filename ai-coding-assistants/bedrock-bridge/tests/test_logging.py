"""Tests for tiered logging: level gating, byte-only scrubbing, the --claude
argv boundary, and the debug-tier consent guard.

Tier selection happens at import time in server.py (it reads
BEDROCK_BRIDGE_LOG_LEVEL when the module is first imported), so the level-gating
tests assert against the already-imported module's helpers via caplog rather
than re-importing under a patched env.
"""

import logging

import pytest

from bedrock_bridge import cli, server


# _scrub_bytes_only masks raw bytes (image data) but leaves text verbatim, so a
# debug dump keeps full prompt text. Contrast with _dump_failure.scrub, which
# also truncates long strings.
def test_scrub_bytes_only_redacts_bytes_keeps_text() -> None:
    long_text = "x" * 1000
    out = server._scrub_bytes_only({"img": b"\xff\xd8\xff\xe0", "prompt": long_text})
    assert out["img"] == "<redacted: 4 bytes>"
    assert out["prompt"] == long_text  # not truncated


def test_scrub_bytes_only_recurses_into_lists_and_dicts() -> None:
    out = server._scrub_bytes_only({"a": [{"b": bytearray(b"12345")}]})
    assert out["a"][0]["b"] == "<redacted: 5 bytes>"


# Incoming Anthropic image blocks carry base64 *strings*, not bytes; those must
# be redacted too (they reach the request-body trace before translation).
def test_scrub_bytes_only_redacts_base64_image_source() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look at this"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA" * 100}},
                ],
            }
        ]
    }
    out = server._scrub_bytes_only(body)
    src = out["messages"][0]["content"][1]["source"]
    assert src["data"] == "<redacted: 400 base64 chars>"
    assert src["media_type"] == "image/png"  # sibling keys kept
    assert out["messages"][0]["content"][0]["text"] == "look at this"  # text verbatim


# The custom TRACE level sits below DEBUG and is the debug tier's content level.
def test_trace_level_below_debug() -> None:
    assert server.TRACE < logging.DEBUG
    assert logging.getLevelName(server.TRACE) == "TRACE"


# The root logger must never be dragged below INFO by our tier, so third-party
# loggers (botocore dumps full signed requests, incl. image bytes, at DEBUG) stay
# gated. Only our own logger's level tracks the tier. (The exact root level can
# be WARNING when another handler pre-empted basicConfig, e.g. under pytest;
# what matters is that it is not DEBUG/TRACE.)
def test_root_logger_not_below_info() -> None:
    assert logging.getLogger().level >= logging.INFO


def test_tier_to_level_mapping() -> None:
    assert server._TIER_TO_LEVEL == {
        "default": logging.INFO,
        "verbose": logging.DEBUG,
        "debug": server.TRACE,
    }


# The logger's own setLevel does the tier gating in production. caplog's handler
# captures everything that survives that gate, so we set the handler to capture
# all (NOTSET) and let logger.setLevel decide what propagates.
def _emit_all_levels() -> None:
    server.logger.info("access line")
    server.logger.debug("verbose detail")
    server._trace("prompt content")


def _captured_levels(caplog: pytest.LogCaptureFixture, level: int) -> set[int]:
    original_level = server.logger.level
    server.logger.setLevel(level)
    try:
        caplog.handler.setLevel(logging.NOTSET)
        with caplog.at_level(logging.NOTSET):
            _emit_all_levels()
        return {r.levelno for r in caplog.records if r.name == "bedrock-bridge"}
    finally:
        server.logger.setLevel(original_level)


# At default tier (INFO): access line only; verbose DEBUG and content TRACE gated.
def test_default_tier_hides_verbose_and_trace(caplog: pytest.LogCaptureFixture) -> None:
    levels = _captured_levels(caplog, logging.INFO)
    assert levels == {logging.INFO}


# At verbose tier (DEBUG): DEBUG shows, content TRACE still gated.
def test_verbose_tier_shows_debug_hides_trace(caplog: pytest.LogCaptureFixture) -> None:
    levels = _captured_levels(caplog, logging.DEBUG)
    assert logging.DEBUG in levels
    assert server.TRACE not in levels


# At debug tier (TRACE): content lines are emitted.
def test_debug_tier_shows_trace(caplog: pytest.LogCaptureFixture) -> None:
    levels = _captured_levels(caplog, server.TRACE)
    assert server.TRACE in levels


# _trace accepts a callable and must NOT invoke it when TRACE is disabled, so
# expensive payload serialization is skipped at default/verbose tiers.
def test_trace_callable_not_evaluated_when_disabled() -> None:
    original_level = server.logger.level
    calls = []
    server.logger.setLevel(logging.INFO)
    try:
        server._trace(lambda: calls.append(1) or "msg")
        assert calls == []  # not evaluated
        server.logger.setLevel(server.TRACE)
        server._trace(lambda: calls.append(1) or "msg")
        assert calls == [1]  # evaluated exactly once
    finally:
        server.logger.setLevel(original_level)


# --claude is a hard argv boundary.
def test_split_at_claude_forwards_everything_after() -> None:
    bridge, claude, passthrough = cli._split_at_claude(
        ["-m", "kimi", "--log-level", "debug", "--claude", "--verbose", "--log-level", "debug"]
    )
    assert bridge == ["-m", "kimi", "--log-level", "debug"]
    assert claude is True
    assert passthrough == ["--verbose", "--log-level", "debug"]


def test_split_at_claude_bridge_flag_after_boundary_is_passthrough() -> None:
    # A bridge flag placed after --claude is forwarded verbatim, never parsed.
    bridge, claude, passthrough = cli._split_at_claude(["--claude", "-m", "kimi"])
    assert bridge == []
    assert claude is True
    assert passthrough == ["-m", "kimi"]


def test_split_at_claude_no_boundary() -> None:
    bridge, claude, passthrough = cli._split_at_claude(["-m", "kimi", "--log-level", "verbose"])
    assert bridge == ["-m", "kimi", "--log-level", "verbose"]
    assert claude is False
    assert passthrough == []


# Debug consent hard-fails on a non-TTY (no bypass by design).
def test_debug_consent_non_tty_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        cli._confirm_debug_logging("/tmp/bedrock-bridge-1234.log")


# Declining the prompt aborts.
def test_debug_consent_declined_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    with pytest.raises(SystemExit):
        cli._confirm_debug_logging("/tmp/bedrock-bridge-1234.log")


# Accepting proceeds (no exception).
@pytest.mark.parametrize("answer", ["y", "Y", "yes", " Yes "])
def test_debug_consent_accepted(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: answer)
    cli._confirm_debug_logging("/tmp/bedrock-bridge-1234.log")  # returns without raising
