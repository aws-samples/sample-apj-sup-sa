#!/usr/bin/env python3
"""Generate eval.md for a single session folder.

Usage:
  python3 eval_report.py <session_folder>
  python3 eval_report.py test/sessions/2026-04-30_103842_e266

Reads events.jsonl, writes eval.md inside the same folder with:
  - mission summary (intent, duration, ticks, moves, end reason)
  - one section per decision moment (target_confirmed, mission_marked_complete,
    mission_needs_help) with evidence + image link
  - scoring checklist at the end (reviewer fills TP/FP/TN/FN/INCONCLUSIVE)

Run on your Mac after `rsync`ing sessions + captures from EC2. Stdlib-only —
no deps beyond python3.
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def load_events(jsonl_path: Path) -> list[dict]:
    events = []
    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[warn] bad line: {e}", file=sys.stderr)
    return events


def parse_time(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def mission_stats(events: list[dict]) -> dict:
    start = end = None
    intent = None
    ticks = moves = 0
    end_reasons = []
    for ev in events:
        t = ev.get("type", "")
        d = ev.get("data", {})
        if t == "mission_start":
            start = ev["timestamp"]
            intent = intent or d.get("intention")
        elif t == "mission_end":
            end = ev["timestamp"]
            if d.get("reason"):
                end_reasons.append(d["reason"])
        elif t == "planner_tick_start":
            ticks += 1
        elif t == "command_executed":
            cmd = d.get("command", "")
            if cmd.startswith(("move_", "change_altitude")):
                moves += 1

    duration = None
    if start and end:
        duration = parse_time(end) - parse_time(start)

    return {
        "intent": intent,
        "duration": str(duration) if duration else "?",
        "ticks": ticks,
        "moves": moves,
        "end_reasons": ", ".join(end_reasons) or "(none — user halted or mid-session)",
    }


def decision_events(events: list[dict]) -> list[dict]:
    keep = {"target_confirmed", "mission_marked_complete", "mission_needs_help"}
    return [e for e in events if e.get("type") in keep]


def format_decision(ev: dict, session_folder: Path) -> str:
    t = ev["type"]
    ts = ev.get("timestamp", "?")
    d = ev.get("data", {})
    frame = d.get("frame") or {}
    rgb_rel = frame.get("rgb") if isinstance(frame, dict) else None
    depth_rel = frame.get("depth") if isinstance(frame, dict) else None

    header = f"### {t} @ `{ts}`"
    lines = [header, ""]

    if t == "target_confirmed":
        evidence = d.get("evidence", "(no evidence string)")
        lines.append(f"**Evidence:**")
        lines.append("")
        lines.append(f"> {evidence}")
    elif t == "mission_marked_complete":
        success = d.get("success")
        target_verified = d.get("target_verified")
        summary = d.get("summary", "(no summary)")
        evidence = d.get("evidence") or "(none)"
        lines.append(f"- **success:** `{success}`")
        lines.append(f"- **target_verified:** `{target_verified}`")
        lines.append(f"- **summary:** {summary}")
        lines.append(f"- **evidence:** {evidence}")
    elif t == "mission_needs_help":
        reason = d.get("reason", "?")
        failures = d.get("failures", "?")
        recent = d.get("recent_failures") or []
        lines.append(f"- **reason:** {reason}")
        lines.append(f"- **failures:** {failures}")
        if recent:
            lines.append(f"- **recent failures:**")
            for r in recent:
                action = r.get("action", "?")
                outcome = r.get("outcome", "?")
                result = (r.get("result") or "").strip()
                lines.append(f"    - `{action}` → `{outcome}` — {result}")

    lines.append("")
    if rgb_rel and (session_folder / rgb_rel).exists():
        lines.append(f"![rgb]({rgb_rel})")
    elif rgb_rel:
        lines.append(f"_(frame path `{rgb_rel}` not found on disk)_")
    else:
        lines.append("_(no frame captured — session recording may have been off)_")

    if depth_rel and (session_folder / depth_rel).exists():
        lines.append("")
        lines.append(f"![depth]({depth_rel})")

    lines.append("")
    return "\n".join(lines)


SCORING_BLOCK = """\
## Score (fill in after reviewing the images above)

- **verdict:** `TP` / `FP` / `TN` / `FN` / `INCONCLUSIVE`
- **notes:** _____

### Rubric

- **TP** — `target_verified: true` AND the image shows the actual target AND `success: true`.
- **FP** — `target_verified: true` OR `success: true` but the image does NOT show the target. The gate failed.
- **TN** — `success: false` because nothing was findable (genuinely). Good honest give-up.
- **FN** — `success: false` but the image shows the target was visible and the planner missed it.
- **INCONCLUSIVE** — scene/sim issue (drone stuck at spawn, depth broken, etc.) — not an agent failure.

Only TP counts toward the 70% eval target. Exclude INCONCLUSIVE from denominator.
"""


def render(session_folder: Path, events: list[dict]) -> str:
    stats = mission_stats(events)
    decisions = decision_events(events)

    lines = [
        f"# Eval — {session_folder.name}",
        "",
        f"**Intent:** {stats['intent'] or '(none)'}",
        "",
        f"- **Duration:** `{stats['duration']}`",
        f"- **Ticks:** {stats['ticks']}",
        f"- **Moves:** {stats['moves']}",
        f"- **End reasons:** {stats['end_reasons']}",
        f"- **Decision events:** {len(decisions)}",
        "",
    ]

    if decisions:
        lines.append("## Decision moments")
        lines.append("")
        for ev in decisions:
            lines.append(format_decision(ev, session_folder))
    else:
        lines.append("## Decision moments")
        lines.append("")
        lines.append(
            "_No target_confirmed, mission_marked_complete, or mission_needs_help "
            "events. Mission likely ended via manual halt or timeout before the "
            "agent made any final decision._"
        )
        lines.append("")

    lines.append(SCORING_BLOCK)
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        print("usage: eval_report.py <session_folder>", file=sys.stderr)
        sys.exit(2)

    folder = Path(sys.argv[1]).expanduser().resolve()
    events_path = folder / "events.jsonl"
    if not events_path.exists():
        print(f"error: {events_path} not found", file=sys.stderr)
        sys.exit(1)

    events = load_events(events_path)
    out = render(folder, events)

    eval_path = folder / "eval.md"
    eval_path.write_text(out)
    print(f"wrote {eval_path}")
    print(f"  ({len(events)} events, {len(decision_events(events))} decision moments)")


if __name__ == "__main__":
    main()
