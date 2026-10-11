#!/usr/bin/env python3
"""Measure whether a recorded call leg contains real audio or silence.

RUNS ON THE ASTERISK INSTANCE. Invoked by scripts/73-echo-test.sh status.

Why this exists: RTP packet counters prove that packets flowed, and they proved
exactly that - 6723 out, 6774 in. What they cannot tell you is whether those
packets carried speech or digital silence, and a G.711 stream of silence looks
identical to a healthy one from the outside. So record both legs and measure the
energy in each. That distinguishes "the network is broken" from "the network is
fine and nothing was speaking", which are opposite problems with opposite fixes.

Usage
    python3 audio-energy.py /tmp/echo-in.wav /tmp/echo-out.wav
"""
from __future__ import annotations

import array
import math
import os
import sys
import wave

# G.711 silence, once decoded to linear, is not exactly zero - it dithers around
# a low value, and comfort noise is deliberately non-zero. So "silent" has to be
# a threshold rather than a test for zero. On a 16-bit scale (max 32767), speech
# on a phone line sits in the high hundreds to low thousands RMS; anything under
# this is not carrying a voice.
SILENCE_RMS = 150.0


def analyse(path: str) -> dict | None:
    if not os.path.exists(path):
        print(f"  {path}: MISSING")
        return None
    if os.path.getsize(path) <= 44:  # header only
        print(f"  {path}: EMPTY (header only, {os.path.getsize(path)} bytes)")
        return None

    with wave.open(path, "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        nframes = w.getnframes()
        raw = w.readframes(nframes)

    if width != 2:
        print(f"  {path}: unexpected sample width {width}, expected 16-bit")
        return None

    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if not samples:
        print(f"  {path}: no samples")
        return None

    peak = max(abs(s) for s in samples)
    rms = math.sqrt(sum(float(s) * s for s in samples) / len(samples))
    secs = nframes / float(rate) if rate else 0.0

    # Fraction of 20ms frames that carry energy. A file can have a high peak from
    # a single click and still be silent overall, so report coverage too - it is
    # what separates "a burst of noise" from "someone talking".
    frame = int(rate * 0.02) or 160
    loud = total = 0
    for i in range(0, len(samples) - frame, frame):
        chunk = samples[i:i + frame]
        f_rms = math.sqrt(sum(float(s) * s for s in chunk) / len(chunk))
        total += 1
        if f_rms > SILENCE_RMS:
            loud += 1
    coverage = (loud / total * 100.0) if total else 0.0

    verdict = "HAS AUDIO" if rms > SILENCE_RMS else "SILENT"
    print(f"  {os.path.basename(path)}: {secs:.1f}s  {rate}Hz  "
          f"rms={rms:7.1f}  peak={peak:6d}  active={coverage:5.1f}%  -> {verdict}")
    return {"rms": rms, "peak": peak, "secs": secs, "coverage": coverage,
            "audio": rms > SILENCE_RMS}


def main() -> int:
    paths = sys.argv[1:]
    if not paths:
        print("usage: audio-energy.py <wav> [wav ...]")
        return 2

    print(f"silence threshold: rms <= {SILENCE_RMS:.0f} on a 16-bit scale")
    results = {p: analyse(p) for p in paths}

    inbound = next((r for p, r in results.items() if "in" in os.path.basename(p)), None)
    outbound = next((r for p, r in results.items() if "out" in os.path.basename(p)), None)

    print()
    print("=== verdict ===")
    if outbound and outbound["audio"] and inbound and inbound["audio"]:
        print("BOTH DIRECTIONS CARRIED AUDIO. The media path is fully proven.")
    elif outbound and outbound["audio"] and inbound and not inbound["audio"]:
        print("WE SENT AUDIO, CALLER SENT SILENCE.")
        print("Our TX is good. Either the caller did not speak, was muted, or the")
        print("carrier is not passing their audio upstream.")
    elif outbound and not outbound["audio"] and inbound and inbound["audio"]:
        print("CALLER SENT AUDIO, WE SENT SILENCE.")
        print("Inbound is good; our playback produced no energy. Suspect the")
        print("Playback source file or a transcode failure.")
    elif outbound and inbound:
        print("BOTH LEGS SILENT despite packets flowing.")
        print("Packets are being exchanged but carry no signal - suspect the")
        print("caller's leg upstream of Chime, since our own Playback should")
        print("always produce energy regardless of the caller.")
    else:
        print("Not enough recordings to judge. Was MixMonitor in the dialplan?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
