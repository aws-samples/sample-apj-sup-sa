#!/usr/bin/env python
"""One trunk, two pjsip endpoints, and the split must not be tidied away.

WHY THIS TEST EXISTS

The Chime trunk is reached by two endpoints that look near-identical, and the
temptation to merge them is obvious to anyone reading the generated pjsip.conf.
Both directions have already broken once, in opposite ways:

  callerid on the SHARED endpoint - applies to channels in BOTH directions, so it
  overwrote the caller ID of INBOUND calls. An inbound caller was identified as our
  own toll-free DID, which is seeded in the CRM as a different customer, so the
  agent answered by greeting Rajesh Kumar in Hindi and reading out his policy
  number. A caller-ID setting for outbound became a data-disclosure bug on inbound.

  NO callerid anywhere - outbound then presents From: "Anonymous" <sip:+1...>. The
  number is valid so Chime originates, and the destination carrier answered 180
  Ringing followed by 486 Busy Here in the same second. Measured, four times.

So: the inbound endpoint must never carry callerid, the outbound endpoint must, and
the outbound endpoint must be unreachable from the network. This asserts against
the SCRIPT that generates the config, because that is the source of truth - the
file on the box is overwritten from it and carries a header saying so.

Run:  ./.venv/bin/python tests/test_asterisk_trunk_config.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "71-asterisk-config.sh"
HANDLER = ROOT / "lambdas" / "outbound" / "handler.py"

INBOUND_EP = "chime"
OUTBOUND_EP = "chime-out"

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:180]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


def pjsip_sections(text: str) -> dict[str, list[str]]:
    """Parse the pjsip.conf heredoc out of the deploy script into sections."""
    body = text.split("cat > /tmp/hvi-pjsip.conf <<PJSIP", 1)[1]
    body = body.split("\nPJSIP", 1)[0]
    sections: dict[str, list[str]] = {}
    current = None
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith(";") or not line:
            continue
        m = re.fullmatch(r"\[([^\]]+)\]", line)
        if m:
            current = m.group(1)
            sections[current] = []
        elif current:
            sections[current].append(line)
    return sections


def main() -> int:
    script = SCRIPT.read_text(encoding="utf-8")
    sections = pjsip_sections(script)

    print("[1] both endpoints exist and are distinct")
    check(f"[{INBOUND_EP}] endpoint is defined", INBOUND_EP in sections,
          str(sorted(sections)))
    check(f"[{OUTBOUND_EP}] endpoint is defined", OUTBOUND_EP in sections,
          str(sorted(sections)))
    if INBOUND_EP not in sections or OUTBOUND_EP not in sections:
        print("\n  cannot continue without both endpoints")
        return 1

    inbound = sections[INBOUND_EP]
    outbound = sections[OUTBOUND_EP]

    print("\n[2] the INBOUND endpoint must not set callerid")
    # This is the data-disclosure guard. callerid here rewrites the identity of
    # whoever calls in, and the CRM lookup then finds the wrong patient.
    offenders = [ln for ln in inbound if ln.lower().startswith("callerid")]
    check(f"[{INBOUND_EP}] has no callerid line", not offenders, str(offenders))
    check(f"[{INBOUND_EP}] still sets from_user, which is what Chime requires",
          any(ln.startswith("from_user=") for ln in inbound), str(inbound))

    print("\n[3] the OUTBOUND endpoint carries an identifiable caller ID")
    cid = [ln for ln in outbound if ln.lower().startswith("callerid=")]
    check(f"[{OUTBOUND_EP}] sets callerid", bool(cid), str(outbound))
    if cid:
        # A number alone leaves the display name as "Anonymous", which is the
        # condition being fixed. Require a name part before the angle brackets.
        value = cid[0].split("=", 1)[1]
        check("callerid has a display name, not just a number",
              "<" in value and value.split("<", 1)[0].strip(), value)
        check("callerid's number is the outbound DID variable",
              "OUTBOUND_CALLER_ID" in value, value)

    print("\n[4] the OUTBOUND endpoint is unreachable from the network")
    # The safety property. An identify section, or an endpoint named after a phone
    # number, would let an inbound INVITE select the endpoint that rewrites caller
    # ID - reintroducing the disclosure bug through the back door.
    identifies = {name: body for name, body in sections.items()
                  if any(ln == "type=identify" for ln in body)}
    targets = {name: [ln.split("=", 1)[1] for ln in body
                      if ln.startswith("endpoint=")]
               for name, body in identifies.items()}
    print(f"    identify sections: { {k: v for k, v in targets.items()} }")
    pointing_at_outbound = [n for n, eps in targets.items() if OUTBOUND_EP in eps]
    check(f"no identify section names {OUTBOUND_EP}", not pointing_at_outbound,
          str(pointing_at_outbound))
    check(f"exactly one identify section, and it names {INBOUND_EP}",
          len(identifies) == 1 and list(targets.values())[0] == [INBOUND_EP],
          str(targets))
    check(f"{OUTBOUND_EP} is not named like a phone number, so username "
          f"identification cannot match it",
          not re.fullmatch(r"\+?[0-9]+", OUTBOUND_EP), OUTBOUND_EP)

    print("\n[5] ip identification is tried first, which is what makes [4] hold")
    glob = sections.get("global", [])
    order = next((ln.split("=", 1)[1] for ln in glob
                  if ln.startswith("endpoint_identifier_order=")), "")
    check("endpoint_identifier_order starts with ip", order.startswith("ip"),
          order or "unset")

    print("\n[6] the dialler originates through the outbound endpoint")
    handler = HANDLER.read_text(encoding="utf-8")
    check("handler defines TRUNK_ENDPOINT", "TRUNK_ENDPOINT" in handler)
    check(f"and it defaults to {OUTBOUND_EP}",
          f'"TRUNK_ENDPOINT", "{OUTBOUND_EP}"' in handler
          or f"'TRUNK_ENDPOINT', '{OUTBOUND_EP}'" in handler,
          "default is not the outbound endpoint")
    # The literal that would silently send calls out of the inbound endpoint again.
    check("no originate hardcodes the inbound endpoint",
          f"@{INBOUND_EP} " not in handler and f"@{INBOUND_EP}\"" not in handler,
          f"found a hardcoded PJSIP/...@{INBOUND_EP}")
    check("originate uses the variable", "@{TRUNK_ENDPOINT}" in handler,
          "the originate line does not interpolate TRUNK_ENDPOINT")

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} trunk-config checks passed.")
    print("  Inbound keeps the caller's identity; outbound presents ours.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
