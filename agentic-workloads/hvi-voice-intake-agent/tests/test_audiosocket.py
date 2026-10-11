#!/usr/bin/env python3
"""Unit tests for the AudioSocket wire protocol.

Framing bugs in a media protocol are silent: the call connects, both sides look
healthy, and the audio is garbage or absent. Debugging that over a live PSTN call
is slow and expensive, so the codec is tested here where a failure is one line of
output.

Run:  ./.venv/bin/python tests/test_audiosocket.py
"""
from __future__ import annotations

import os
import struct
import sys
import uuid as uuidlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from media.audiosocket import (  # noqa: E402
    FRAME_BYTES,
    TYPE_AUDIO,
    TYPE_ERROR,
    TYPE_TERMINATE,
    TYPE_UUID,
    AudioSocketProtocolError,
    decode,
    encode,
    encode_audio,
    parse_uuid,
)

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {label}")


def ok(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        failures.append(f"{label} {detail}")
        print(f"  FAIL {label} {detail}")
    else:
        print(f"  ok   {label}")


print("encode: header layout")
msg = encode(TYPE_AUDIO, b"\x01\x02\x03\x04")
check("3-byte header plus payload", len(msg), 3 + 4)
check("type byte", msg[0], TYPE_AUDIO)
check("length is big-endian", struct.unpack("!H", msg[1:3])[0], 4)
check("payload preserved", msg[3:], b"\x01\x02\x03\x04")

print()
print("encode: terminate carries no payload")
check("terminate is header only", encode(TYPE_TERMINATE), b"\x00\x00\x00")

print()
print("encode: oversized payload is rejected")
# The length field is 16 bits, so anything larger cannot be represented. Failing
# loudly beats silently truncating a caller's audio.
try:
    encode(TYPE_AUDIO, b"\x00" * 70000)
    ok("raises on payload above 65535", False, "no exception")
except AudioSocketProtocolError:
    ok("raises on payload above 65535", True)

print()
print("decode: round trip")
parsed = decode(encode_audio(b"abcd"))
ok("decodes a complete message", parsed is not None)
if parsed:
    t, payload, rest = parsed
    check("type", t, TYPE_AUDIO)
    check("payload", payload, b"abcd")
    check("no remainder", rest, b"")

print()
print("decode: partial reads return None rather than guessing")
# TCP delivers a byte stream, not messages. Every real connection will split a
# message across reads, so this is the common case and not an edge case.
full = encode_audio(b"12345678")
for cut in (0, 1, 2, 3, 5, len(full) - 1):
    ok(f"incomplete at {cut} bytes yields None", decode(full[:cut]) is None)

print()
print("decode: multiple messages in one read")
stream = encode(TYPE_UUID, uuidlib.uuid4().bytes) + encode_audio(b"aaaa") + encode_audio(b"bbbb")
types = []
buf = stream
while True:
    p = decode(buf)
    if p is None:
        break
    t, _, buf = p
    types.append(t)
check("all three messages recovered", types, [TYPE_UUID, TYPE_AUDIO, TYPE_AUDIO])
check("buffer fully drained", buf, b"")

print()
print("decode: trailing partial message is left in the buffer")
stream = encode_audio(b"aaaa") + encode_audio(b"bb")[:4]
p = decode(stream)
ok("first message decodes", p is not None)
if p:
    _, _, rest = p
    ok("partial tail retained for the next read", decode(rest) is None and len(rest) == 4)

print()
print("parse_uuid: both wire forms")
u = uuidlib.uuid4()
check("16 raw bytes", parse_uuid(u.bytes), str(u))
# Some Asterisk builds send the text form. Accepting both avoids dropping a call
# over a formatting difference.
check("36-character text", parse_uuid(str(u).encode()), str(u))
try:
    parse_uuid(b"not-a-uuid")
    ok("rejects garbage", False, "no exception")
except AudioSocketProtocolError:
    ok("rejects garbage", True)

print()
print("frame sizing")
# 8kHz, 16-bit, mono, 20ms = 160 samples = 320 bytes. Asterisk expects exactly
# this; larger writes make playback stutter.
check("20ms frame is 320 bytes", FRAME_BYTES, 320)

print()
print("error type is distinguishable")
t, payload, _ = decode(encode(TYPE_ERROR, b"\x03"))
check("error type", t, TYPE_ERROR)
check("error code", payload, b"\x03")

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("AudioSocket protocol tests passed")
