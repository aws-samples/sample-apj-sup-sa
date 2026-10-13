"""Automated quality screen + bulk-approve for the question bank (production breadth, US4 / #39).

The vetted bank's normal gate is HUMAN review (bank/review.py, FR-206 / Principle II): a draft is
invisible to selection until a person approves it. At production breadth (`bank/generate.py --variants`
produces ~200+ drafts) per-question human review is impractical, so this tool applies a deterministic
AUTOMATED screen and bulk-approves what passes.

CONSTITUTION NOTE (Principle II / FR-206): this DELIBERATELY RELAXES per-question human review for the
bulk-generated population — exactly the same trade-off, and the same product reason, as the JIT
uncovered-role path (see backend/src/prep/jit_generate.py). It is recorded HONESTLY and auditably:
every auto-approved row is stamped scoring_guidance.review='auto-screen-v1' so it is distinguishable
from a human-vetted ('approved' with no review marker) row, and the screen's rejections are RETIRED
(terminal), never silently dropped. A human can still spot-check or retire any auto-approved row later.

The screen is three deterministic checks (NO LLM, NO network) run per draft:
  1. format/length      — prompt 25..600 chars, ends like a question/prompt, >= 2 follow-up probes
  2. STAR-probe shape   — follow-up probes look like real drill-downs (not empty/duplicated)
  3. near-duplicate     — within the SAME (role_family|category, competency, difficulty) group, drop a
                          draft whose normalized prompt is too similar to one already kept (so depth
                          means distinct questions, not paraphrases) — the first seen in id order wins.

Entirely OFF any session path — an operator step run after bank/generate.py, before bank/embed.py.

Run:
  python -m bank.screen --dry-run                 # report pass/fail/dup per draft; no writes
  python -m bank.screen                           # approve passers (auto-screen-v1), retire failures
  python -m bank.screen --source generated        # restrict to generated drafts (default: all drafts)
  python -m bank.screen --keep-failures           # approve passers but leave failures as draft
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from difflib import SequenceMatcher

import asyncpg
from dotenv import load_dotenv

load_dotenv()

_REVIEW_MARKER = "auto-screen-v1"

# Screen thresholds (deterministic; tuned for the Sonnet-generated bank).
_MIN_PROMPT = 25
_MAX_PROMPT = 600
_MIN_PROBES = 2
_DUP_RATIO = 0.82  # normalized-prompt similarity at/above which a draft is a near-duplicate within its group


def _normalize(text: str) -> str:
    """Lowercased, punctuation-stripped, whitespace-collapsed form for similarity comparison."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


def _as_list(raw) -> list:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return raw or []


def _as_dict(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _format_reason(prompt: str, probes: list) -> str | None:
    """Return a rejection reason string, or None if the draft passes the format/STAR checks."""
    p = (prompt or "").strip()
    if not (_MIN_PROMPT <= len(p) <= _MAX_PROMPT):
        return f"prompt length {len(p)} outside [{_MIN_PROMPT},{_MAX_PROMPT}]"
    cleaned = [str(x).strip() for x in probes if str(x).strip()]
    if len(cleaned) < _MIN_PROBES:
        return f"only {len(cleaned)} follow-up probe(s) (need >= {_MIN_PROBES})"
    if len({_normalize(x) for x in cleaned}) < len(cleaned):
        return "duplicate follow-up probes"
    return None


def _group_key(row: dict) -> tuple:
    """Near-duplicate scope: same role/category, competency, difficulty — depth within a slot."""
    return (row.get("role_family") or row.get("category"), row["competency"], row["difficulty"])


def screen_drafts(rows: list[dict]) -> dict:
    """Pure screen over draft rows (ordered by id for determinism). Returns
    {'approve': [ids], 'retire': [(id, reason)]}. The first acceptable draft in each group is kept;
    later near-duplicates in that group are retired."""
    approve: list[str] = []
    retire: list[tuple[str, str]] = []
    kept_by_group: dict[tuple, list[str]] = {}
    for row in rows:
        rid = str(row["id"])
        prompt = row["prompt_template"]
        probes = _as_list(row["follow_up_prompts"])
        reason = _format_reason(prompt, probes)
        if reason:
            retire.append((rid, reason))
            continue
        norm = _normalize(prompt)
        gk = _group_key(row)
        dup_of = None
        for kept_norm in kept_by_group.get(gk, []):
            if SequenceMatcher(None, norm, kept_norm).ratio() >= _DUP_RATIO:
                dup_of = kept_norm
                break
        if dup_of is not None:
            retire.append((rid, "near-duplicate within slot"))
            continue
        kept_by_group.setdefault(gk, []).append(norm)
        approve.append(rid)
    return {"approve": approve, "retire": retire}


async def _load_drafts(conn: asyncpg.Connection, source: str | None) -> list[dict]:
    q = """
        SELECT id, category, competency, question_type, difficulty, role_family,
               source, prompt_template, follow_up_prompts, scoring_guidance
        FROM question_archetype
        WHERE status = 'draft'
    """
    args: list = []
    if source:
        q += " AND source = $1"
        args.append(source)
    q += " ORDER BY id"  # deterministic: first-seen wins on near-duplicates
    return [dict(r) for r in await conn.fetch(q, *args)]


async def _apply(conn: asyncpg.Connection, plan: dict, keep_failures: bool) -> None:
    """Approve passers (stamping the auto-screen marker into scoring_guidance) and retire failures."""
    async with conn.transaction():
        for rid in plan["approve"]:
            await conn.execute(
                """
                UPDATE question_archetype
                SET status = 'approved',
                    active = TRUE,
                    scoring_guidance = COALESCE(scoring_guidance, '{}'::jsonb)
                                       || jsonb_build_object('review', $2::text)
                WHERE id = $1 AND status = 'draft'
                """,
                rid, _REVIEW_MARKER,
            )
        if not keep_failures:
            for rid, _reason in plan["retire"]:
                await conn.execute(
                    "UPDATE question_archetype SET status='retired', active=FALSE "
                    "WHERE id=$1 AND status='draft'",
                    rid,
                )


async def run(database_url: str, source: str | None, dry_run: bool, keep_failures: bool) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        drafts = await _load_drafts(conn, source)
        if not drafts:
            print(f"no draft archetypes{f' with source={source!r}' if source else ''}.")
            return
        plan = screen_drafts(drafts)
        print(f"screened {len(drafts)} draft(s): {len(plan['approve'])} pass -> approve, "
              f"{len(plan['retire'])} fail -> {'retire' if not keep_failures else 'left as draft'}.")
        for rid, reason in plan["retire"]:
            print(f"  - retire {rid}: {reason}")
        if dry_run:
            print("\n(dry-run — no writes. Re-run without --dry-run to apply, then bank/embed.py.)")
            return
        await _apply(conn, plan, keep_failures)
        print(f"\nApproved {len(plan['approve'])} archetype(s) with review='{_REVIEW_MARKER}'; "
              f"{0 if keep_failures else len(plan['retire'])} retired. "
              f"Run bank/embed.py to embed the approved rows before they are selectable.")
    finally:
        await conn.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Automated quality screen + bulk-approve (US4 production breadth).")
    p.add_argument("--source", help="restrict to drafts with this source (e.g. generated)")
    p.add_argument("--dry-run", action="store_true", help="report pass/fail/dup without writing")
    p.add_argument("--keep-failures", action="store_true",
                   help="approve passers but leave failing drafts as draft (do not retire)")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; cannot reach the question bank.")
    await run(database_url, args.source, args.dry_run, args.keep_failures)


if __name__ == "__main__":
    asyncio.run(_main())
