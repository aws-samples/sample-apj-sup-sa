"""Question-bank approval gate (T036) — bank-generation-contract.md / FR-206.

The mandatory HUMAN review step between offline generation (bank/generate.py) and embedding
(bank/embed.py). A generated/curated archetype starts `status='draft'` and is INVISIBLE to the
session-prep selection query until a human approves it here; embedding only happens after approval.
This CLI is the only sanctioned way to move an archetype's status, and it enforces the closed set
of transitions:

    draft     -> approved   (reviewer accepts; becomes eligible once embedded)
    draft     -> retired    (reviewer rejects)
    approved  -> retired    (withdraw/supersede a previously-approved question)

No other transition is permitted — in particular a `retired` row is terminal (never resurrected)
and `approved -> draft` is refused. This is what "vetted" means operationally (AC-2d): an
unapproved question can never reach a session, and a rejected one is excluded from all future plans
(US4 scenarios 1 & 3).

There is NO auto-approval. This is an operator entry point only, never invoked from the live or
async session path.

Run:
  python -m bank.review --list                      # pending drafts (default), newest first
  python -m bank.review --list --status approved     # list a different status
  python -m bank.review --show <id>                  # full prompt + probes + guidance for one row
  python -m bank.review --approve <id> [<id> ...]    # draft -> approved
  python -m bank.review --retire  <id> [<id> ...]    # draft|approved -> retired
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid

import asyncpg
from dotenv import load_dotenv

load_dotenv()

# Closed transition table (FR-206). A status not present as a key is terminal.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"approved", "retired"},
    "approved": {"retired"},
    "retired": set(),
}


def _as_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise SystemExit(f"not a valid archetype id: {raw!r}")


# --- Read paths -----------------------------------------------------------------------------------


async def _list(conn: asyncpg.Connection, status: str) -> None:
    rows = await conn.fetch(
        """
        SELECT id, category, competency, question_type, difficulty,
               role_family, source, prompt_template
        FROM question_archetype
        WHERE status = $1
        ORDER BY created_at DESC, id
        """,
        status,
    )
    if not rows:
        print(f"no archetypes with status={status!r}.")
        return
    print(f"{len(rows)} archetype(s) with status={status!r}:")
    for r in rows:
        axis = r["role_family"] or r["category"]
        print(
            f"  {r['id']}  {axis}/{r['competency']}/{r['question_type']}/{r['difficulty']} "
            f"[{r['source']}]\n      {r['prompt_template'][:90]}"
        )


async def _show(conn: asyncpg.Connection, archetype_id: uuid.UUID) -> None:
    row = await conn.fetchrow(
        """
        SELECT id, status, category, competency, question_type, difficulty,
               industry, role_family, seniority, source,
               prompt_template, follow_up_prompts, scoring_guidance,
               embedding IS NOT NULL AS embedded, embedding_model
        FROM question_archetype WHERE id = $1
        """,
        archetype_id,
    )
    if row is None:
        raise SystemExit(f"no archetype with id {archetype_id}")
    probes = json.loads(row["follow_up_prompts"]) if isinstance(row["follow_up_prompts"], str) else row["follow_up_prompts"]
    guidance = json.loads(row["scoring_guidance"]) if isinstance(row["scoring_guidance"], str) else row["scoring_guidance"]
    print(f"id:            {row['id']}")
    print(f"status:        {row['status']}  (embedded={row['embedded']}, model={row['embedding_model']})")
    print(f"taxonomy:      category={row['category']} competency={row['competency']} "
          f"question_type={row['question_type']} difficulty={row['difficulty']}")
    print(f"domain axis:   industry={row['industry']} role_family={row['role_family']} "
          f"seniority={row['seniority']} source={row['source']}")
    print(f"\nprompt_template:\n  {row['prompt_template']}")
    print("\nfollow_up_prompts:")
    for p in probes or []:
        print(f"  - {p}")
    print("\nscoring_guidance:")
    print(f"  strong: {(guidance or {}).get('strong', '')}")
    print(f"  weak:   {(guidance or {}).get('weak', '')}")


# --- Write path (the gate) ------------------------------------------------------------------------


async def _transition(conn: asyncpg.Connection, archetype_id: uuid.UUID, target: str) -> str:
    """Move one archetype to `target` status iff the transition is allowed. Returns a result line.

    Runs in a transaction with SELECT ... FOR UPDATE so a concurrent reviewer cannot race the
    status check against the write.
    """
    async with conn.transaction():
        current = await conn.fetchval(
            "SELECT status FROM question_archetype WHERE id = $1 FOR UPDATE", archetype_id
        )
        if current is None:
            return f"  ! {archetype_id}: no such archetype — skipped"
        if current == target:
            return f"  = {archetype_id}: already {target} — no change"
        if target not in _ALLOWED_TRANSITIONS.get(current, set()):
            return f"  ! {archetype_id}: illegal transition {current} -> {target} — refused"
        # Retiring clears `active`; the partial IVFFlat index + the prep filter already exclude
        # non-approved rows, but flipping active keeps any active-based query honest too.
        await conn.execute(
            "UPDATE question_archetype SET status = $2, active = ($2 <> 'retired') WHERE id = $1",
            archetype_id,
            target,
        )
        return f"  + {archetype_id}: {current} -> {target}"


async def review(database_url: str, args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        if args.list is not None:
            await _list(conn, args.status)
            return
        if args.show:
            await _show(conn, _as_uuid(args.show))
            return
        target = "approved" if args.approve else "retired"
        ids = args.approve or args.retire
        results = [await _transition(conn, _as_uuid(raw), target) for raw in ids]
        for line in results:
            print(line)
        applied = sum(1 for line in results if line.startswith("  +"))
        refused = sum(1 for line in results if line.startswith("  !"))
        print(f"\n{applied} transition(s) applied to {target}; {refused} refused/skipped.")
        if target == "approved" and applied:
            print("Run bank/embed.py to embed the newly approved archetypes before they are selectable.")
    finally:
        await conn.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Question-bank approval gate (T036, FR-206).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", nargs="?", const=True, default=None,
                   help="list archetypes by status (see --status; default draft)")
    g.add_argument("--show", metavar="ID", help="print the full text of one archetype")
    g.add_argument("--approve", nargs="+", metavar="ID", help="draft -> approved for each id")
    g.add_argument("--retire", nargs="+", metavar="ID", help="draft|approved -> retired for each id")
    p.add_argument("--status", default="draft", choices=("draft", "approved", "retired"),
                   help="status filter for --list (default draft)")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; cannot reach the question bank.")
    await review(database_url, args)


if __name__ == "__main__":
    asyncio.run(_main())
