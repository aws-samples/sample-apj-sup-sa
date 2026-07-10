"""Offline question-bank generation (T034) — contracts/bank-generation-contract.md.

An OPERATOR-triggered batch, never on the live or async session path (so generation costs no
session latency or per-session spend). Reads the two-axis taxonomy seed (bank/seed/taxonomy.json,
T035) and, for each (family x competency-entry x difficulty) slot, asks Bedrock to write a candidate
interview question with its seed funnel probes and G3 scoring guidance. Each result is inserted into
`question_archetype` as `source='generated', status='draft'` — it is NOT eligible for any session
until a human approves it (bank/review.py, FR-206) and it is embedded (bank/embed.py).

Cost control (Part 5 cost flag): generation is a BOUNDED batch. `--limit` caps the number of slots
generated per run (default 12); re-running with a higher limit deterministically widens coverage
because slots are expanded in a fixed taxonomy order and already-present (family,competency,difficulty)
draft/approved rows are skipped. `--family` restricts to one family. There is no auto-approval and no
path that invokes this from POST /sessions or the live loop.

Run:
  python -m bank.generate --dry-run                       # expand slots + show the plan, no Bedrock/DB
  python -m bank.generate --limit 6 --family general      # generate 6 general slots into the DB
  python -m bank.generate                                 # generate up to --limit slots (default 12)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

_TAXONOMY_PATH = Path(__file__).parent / "seed" / "taxonomy.json"

# Stable namespace so a (family,competency,question_type,difficulty) slot always maps to the same
# archetype id — re-running generation upserts the same draft rather than duplicating it.
_GEN_NS = uuid.UUID("6f4c0d2e-0000-4002-a010-000000000034")

_DEFAULT_LIMIT = 12  # bounded batch: cap slots generated per run (cost flag)


def _slot_id(family_key: str, competency: str, question_type: str, difficulty: str, variant: int = 0) -> str:
    """Stable id for a (family,competency,question_type,difficulty[,variant]) slot.

    variant 0 maps to the LEGACY id (no suffix) so re-running over already-seeded rows is idempotent
    and never duplicates the original single-per-slot bank; variant >= 1 gets a ':v<n>' suffix so a
    higher --variants deterministically ADDS distinct questions to the same slot (production breadth)."""
    base = f"{family_key}:{competency}:{question_type}:{difficulty}"
    if variant:
        base = f"{base}:v{variant}"
    return str(uuid.uuid5(_GEN_NS, base))


def _load_taxonomy() -> list[dict]:
    data = json.loads(_TAXONOMY_PATH.read_text())
    families = data.get("families") or []
    if not families:
        raise SystemExit("taxonomy.json has no families")
    return families


def expand_slots(families: list[dict], only_family: str | None = None, variants: int = 1) -> list[dict]:
    """Expand the taxonomy into a flat, DETERMINISTICALLY-ORDERED list of generation slots.

    `variants` slots per (family x competency-entry x difficulty) — variant 0 carries the legacy id
    (so the original single-per-slot bank is preserved on a re-run), variants 1..N-1 get distinct
    ':v<n>' ids and a per-variant index used to ask for a DIFFERENT angle on the same competency.
    Order is taxonomy order, then variant order, so both `--limit` and `--variants` are reproducible
    and re-runs extend coverage/depth rather than reshuffling it.
    """
    slots: list[dict] = []
    for fam in families:
        if only_family and fam["key"] != only_family:
            continue
        themes = fam.get("themes") or []
        for entry in fam.get("competencies") or []:
            for difficulty in fam.get("difficulties") or []:
                for variant in range(max(1, variants)):
                    # Pin each variant >= 1 to a distinct theme so independent generations diverge
                    # (without a theme the model converges on the single most salient scenario per
                    # slot). variant 0 stays theme-free for a legacy-stable, free-form question.
                    theme = themes[(variant - 1) % len(themes)] if (variant and themes) else None
                    slots.append(
                        {
                            "id": _slot_id(fam["key"], entry["competency"], entry["question_type"],
                                           difficulty, variant),
                            "family_key": fam["key"],
                            "category": fam["category"],
                            "competency": entry["competency"],
                            "question_type": entry["question_type"],
                            "industry": fam.get("industry"),
                            "role_family": fam.get("role_family"),
                            "seniority": fam.get("seniority"),
                            "difficulty": difficulty,
                            "variant": variant,
                            "theme": theme,
                            "label": fam.get("label", fam["key"]),
                            "domain_context": fam.get("domain_context", ""),
                        }
                    )
    return slots


# --- Bedrock generation ---------------------------------------------------------------------------

_GEN_SYSTEM = (
    "You are an expert interview designer building a vetted question bank for a practice-interview "
    "product. You write ONE behavioral/situational/technical interview question at a time, following "
    "the STAR + funnel methodology: an open main question, then seed follow-up probes that drill for "
    "missing specifics (situation/task/action/result) WITHOUT leading the candidate to an answer. "
    "The main question must be answerable by drawing on the candidate's OWN experience (it is "
    "personalized to their resume + the target role at session time), so it must NOT hard-code any "
    "company, product, or fact. Calibrate to the requested difficulty tier:\n"
    "  easy      = gentle, one concrete behavioral prompt, scaffolding allowed.\n"
    "  moderate  = expects structured reasoning and a real trade-off.\n"
    "  difficult = expects depth, handles ambiguity/pressure, no scaffolding.\n"
    "Respond with ONLY a JSON object, no prose:\n"
    '{"prompt_template": <string, the main question>, '
    '"follow_up_prompts": [<2-4 short probe strings>], '
    '"scoring_guidance": {"strong": <string>, "weak": <string>}}'
)


def _build_gen_user_msg(slot: dict) -> str:
    axis = (
        f"General competency (cross-role); ground in the candidate's general experience"
        if slot["category"] == "general"
        else f"Domain/Industry: {slot['label']} (industry={slot['industry']}, "
        f"role_family={slot['role_family']}); domain context: {slot['domain_context']}"
    )
    # For depth (--variants > 1) each variant must explore a DIFFERENT angle of the same competency
    # so a 30/45-min interview draws distinct questions rather than re-asking one. variant 0 has no
    # extra instruction (keeps the legacy single-per-slot question byte-stable on a re-run); variants
    # >= 1 are pinned to a distinct theme so independent generations actually diverge.
    variant = slot.get("variant", 0)
    theme = slot.get("theme")
    if not variant:
        distinct = ""
    elif theme:
        distinct = (
            f"- This question MUST center on this specific scenario: {theme}. Probe the "
            f"'{slot['competency']}' competency through that scenario; do not drift to a generic prompt.\n"
        )
    else:
        distinct = (
            f"- This is variant #{variant + 1} for this slot: write a question that probes the SAME "
            f"competency from a clearly DIFFERENT angle/scenario than the other variants (e.g. a "
            f"different situation, stakeholder, or constraint). Do not paraphrase a generic prompt.\n"
        )
    return (
        f"Write one interview question for this slot:\n"
        f"- Axis: {axis}\n"
        f"- Competency: {slot['competency']}\n"
        f"- Question type: {slot['question_type']}\n"
        f"- Difficulty tier: {slot['difficulty']}\n"
        f"{distinct}\n"
        "Return the JSON object only."
    )


def _parse_gen_json(text: str) -> dict | None:
    """Extract the generator's JSON defensively; None if it cannot be parsed/validated."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    prompt = (obj.get("prompt_template") or "").strip()
    if not prompt:
        return None
    probes = [str(p).strip() for p in (obj.get("follow_up_prompts") or []) if str(p).strip()]
    guidance = obj.get("scoring_guidance") or {}
    if not isinstance(guidance, dict):
        guidance = {}
    return {
        "prompt_template": prompt,
        "follow_up_prompts": probes,
        "scoring_guidance": {
            "strong": str(guidance.get("strong") or "").strip(),
            "weak": str(guidance.get("weak") or "").strip(),
        },
    }


def _bedrock_generate(client, model_id: str, slot: dict) -> dict | None:
    resp = client.converse(
        modelId=model_id,
        system=[{"text": _GEN_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": _build_gen_user_msg(slot)}]}],
        inferenceConfig={"maxTokens": 700, "temperature": 0.7},
    )
    return _parse_gen_json(resp["output"]["message"]["content"][0]["text"])


# --- Persistence ----------------------------------------------------------------------------------


async def _existing_slot_ids(conn: asyncpg.Connection, slot_ids: list[str]) -> set[str]:
    """Slot ids already present as a draft/approved/retired row — skipped so a re-run extends
    coverage rather than regenerating (and never resurrecting a retired question)."""
    if not slot_ids:
        return set()
    rows = await conn.fetch(
        "SELECT id FROM question_archetype WHERE id = ANY($1::uuid[])",
        [uuid.UUID(s) for s in slot_ids],
    )
    return {str(r["id"]) for r in rows}


async def _insert_draft(conn: asyncpg.Connection, slot: dict, generated: dict) -> None:
    """Insert one generated candidate as source='generated', status='draft'. No embedding yet
    (embed-on-approve, R3). ON CONFLICT keeps an existing row untouched (idempotent re-run)."""
    await conn.execute(
        """
        INSERT INTO question_archetype
            (id, category, competency, question_type, industry, role_family, seniority,
             difficulty, prompt_template, follow_up_prompts, scoring_guidance,
             embedding, embedding_model, source, status, version, active)
        VALUES
            ($1, $2, $3, $4, $5, $6, $7,
             $8, $9, $10::jsonb, $11::jsonb,
             NULL, NULL, 'generated', 'draft', 1, TRUE)
        ON CONFLICT (id) DO NOTHING
        """,
        uuid.UUID(slot["id"]),
        slot["category"],
        slot["competency"],
        slot["question_type"],
        slot["industry"],
        slot["role_family"],
        slot["seniority"],
        slot["difficulty"],
        generated["prompt_template"],
        json.dumps(generated["follow_up_prompts"]),
        json.dumps(generated["scoring_guidance"]),
    )


async def generate(
    database_url: str,
    model_id: str,
    region: str,
    limit: int,
    only_family: str | None,
    variants: int = 1,
) -> None:
    families = _load_taxonomy()
    slots = expand_slots(families, only_family, variants)
    if not slots:
        raise SystemExit(f"no slots for family={only_family!r}")

    import boto3  # lazy: only needed for the real generation path

    client = boto3.client("bedrock-runtime", region_name=region)
    conn = await asyncpg.connect(database_url)
    loop = asyncio.get_running_loop()
    try:
        present = await _existing_slot_ids(conn, [s["id"] for s in slots])
        pending = [s for s in slots if s["id"] not in present][:limit]
        skipped = len(slots) - len([s for s in slots if s["id"] not in present])
        inserted = failed = 0
        for slot in pending:
            generated = await loop.run_in_executor(None, _bedrock_generate, client, model_id, slot)
            if generated is None:
                failed += 1
                print(f"  ! generation returned no parseable JSON for {slot['family_key']}/"
                      f"{slot['competency']}/{slot['difficulty']} — skipped")
                continue
            await _insert_draft(conn, slot, generated)
            inserted += 1
            print(f"  + draft {slot['family_key']}/{slot['competency']}/{slot['question_type']}/"
                  f"{slot['difficulty']}: {generated['prompt_template'][:70]}")
        print(
            f"\nGenerated {inserted} draft archetype(s) "
            f"({failed} generation failure(s), {skipped} already present); "
            f"{len(slots)} total slots across {len({s['family_key'] for s in slots})} families. "
            f"All status='draft' — run bank/review.py to approve, then bank/embed.py."
        )
    finally:
        await conn.close()


def _dry_run(only_family: str | None, limit: int, variants: int) -> None:
    """Expand + print the generation plan without touching Bedrock or the DB."""
    families = _load_taxonomy()
    slots = expand_slots(families, only_family, variants)
    fams = sorted({s["family_key"] for s in slots})
    print(
        f"taxonomy: {len(families)} families "
        f"({sum(1 for f in families if f['category'] == 'general')} general + "
        f"{sum(1 for f in families if f['category'] == 'domain')} domain); "
        f"{variants} variant(s)/slot -> {len(slots)} generation slots; --limit {limit} would "
        f"generate the first {min(limit, len(slots))}."
    )
    print(f"families in scope: {', '.join(fams)}")
    for s in slots[:limit]:
        print(f"  - {s['family_key']}/{s['competency']}/{s['question_type']}/{s['difficulty']}"
              f"/v{s['variant']} (id={s['id'][:8]}...)")
    if len(slots) > limit:
        print(f"  ... and {len(slots) - limit} more slot(s) beyond --limit")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline bulk question-bank generation (T034).")
    p.add_argument("--limit", type=int, default=_DEFAULT_LIMIT,
                   help=f"bounded batch: max slots to generate this run (cost flag; default {_DEFAULT_LIMIT})")
    p.add_argument("--variants", type=int, default=1,
                   help="distinct questions to generate per (family,competency,difficulty) slot — "
                        "raise for production breadth so 30/45-min interviews draw deep distinct "
                        "questions (default 1; variant 0 keeps the legacy id, so re-runs are additive)")
    p.add_argument("--family", help="restrict generation to one taxonomy family key (e.g. general)")
    p.add_argument("--dry-run", action="store_true",
                   help="expand and print the generation plan without calling Bedrock or the DB")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    if args.dry_run:
        _dry_run(args.family, args.limit, args.variants)
        return
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; cannot write draft archetypes.")
    model_id = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6")
    region = os.environ.get("AWS_REGION", "us-east-1")
    await generate(database_url, model_id, region, args.limit, args.family, args.variants)


if __name__ == "__main__":
    asyncio.run(_main())
