"""Resume parsing (T011) — OFF the response_gap clock (R4).

Two stages, both in the setup window (never live):
  1. Deterministic text extraction from the uploaded PDF/DOCX (pypdf / python-docx).
  2. A Bedrock structured-extraction call that turns the raw text into the parsed-facts JSON
     shown for confirm/correct (FR-201).

The CONFIRMED facts (not this raw parse) ground the interview (FR-204) — this module only
produces the candidate facts + a confidence signal. On low confidence or an unparseable file
the caller drops to the manual-entry fallback so setup still completes (SC-006).

Privacy (Constitution III / FR-218): raw resume text and parsed facts are PII. They are returned
to the caller (which stores them in RDS/S3 under consent) and are NEVER logged here.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from typing import Literal

from .config import settings

log = logging.getLogger("backend")

ParseStatus = Literal["parsed", "low_confidence", "failed"]
Confidence = Literal["high", "medium", "low"]

# The parsed-facts shape the SPA confirms (setup-api.md). Kept small and structured so it maps
# cleanly into users.resume_parsed_facts (JSONB) and into resume_highlights at session prep.
_EXTRACTION_INSTRUCTION = (
    "You extract structured facts from a candidate's resume for an interview-prep tool. "
    "Return ONLY valid JSON matching this schema, with no prose:\n"
    "{\n"
    '  "name": string|null,\n'
    '  "summary": string|null,\n'
    '  "skills": string[],\n'
    '  "experience": [ {"title": string, "organization": string|null, '
    '"duration": string|null, "highlights": string[]} ],\n'
    '  "education": [ {"qualification": string, "institution": string|null, '
    '"year": string|null} ],\n'
    '  "confidence": "high"|"medium"|"low"\n'
    "}\n"
    "Use null/empty arrays for anything genuinely absent. Do not invent facts. Set confidence "
    "to 'low' if the text is sparse, garbled, or looks like a scanned image with little text."
)


@dataclass
class ParseResult:
    parsed_facts: dict
    parse_status: ParseStatus
    confidence: Confidence


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Deterministically extract plain text from a PDF or DOCX (stage 1). No LLM, no network.

    Raises ValueError for an unsupported/empty file so the caller can drop to manual entry.
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        text = _extract_pdf(file_bytes)
    elif lower.endswith(".docx"):
        text = _extract_docx(file_bytes)
    elif lower.endswith(".txt"):
        text = file_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"unsupported resume format: {filename!r} (use PDF, DOCX, or TXT)")
    text = text.strip()
    if not text:
        raise ValueError("no extractable text (file may be a scanned image)")
    return text


def _extract_pdf(file_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(file_bytes: bytes) -> str:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in document.paragraphs)


def _empty_facts() -> dict:
    return {
        "name": None,
        "summary": None,
        "skills": [],
        "experience": [],
        "education": [],
    }


def structured_extract(resume_text: str) -> ParseResult:
    """Stage 2: Bedrock structured extraction of the raw text into parsed-facts JSON.

    Off the gap clock (setup window). Any failure degrades to `failed` with empty facts so the
    caller can show the manual-entry fallback rather than blocking setup (SC-006). Never logs the
    resume text or the extracted facts (FR-218).
    """
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        resp = client.converse(
            modelId=settings.bedrock_model_id,
            system=[{"text": _EXTRACTION_INSTRUCTION}],
            messages=[{"role": "user", "content": [{"text": resume_text[:20000]}]}],
            inferenceConfig={"maxTokens": 1500, "temperature": 0.0},
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        facts = _coerce_facts(raw)
    except Exception as exc:  # noqa: BLE001 - never surface PII; degrade to manual entry
        log.warning("resume structured-extraction failed (%s); falling back to manual entry", type(exc).__name__)
        return ParseResult(parsed_facts=_empty_facts(), parse_status="failed", confidence="low")

    confidence: Confidence = facts.pop("confidence", "medium") or "medium"
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    status: ParseStatus = "parsed" if confidence in ("high", "medium") else "low_confidence"
    return ParseResult(parsed_facts=facts, parse_status=status, confidence=confidence)


def _coerce_facts(raw: str) -> dict:
    """Parse the model's JSON, tolerating a code-fence wrapper, into the expected shape."""
    text = raw.strip()
    if text.startswith("```"):
        # strip a ```json ... ``` fence if the model added one
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    data = json.loads(text)
    facts = _empty_facts()
    facts["name"] = data.get("name")
    facts["summary"] = data.get("summary")
    facts["skills"] = list(data.get("skills") or [])
    facts["experience"] = list(data.get("experience") or [])
    facts["education"] = list(data.get("education") or [])
    if "confidence" in data:
        facts["confidence"] = data["confidence"]
    return facts


def parse_resume(file_bytes: bytes, filename: str) -> ParseResult:
    """Full off-gap-clock parse: extract text, then structured-extract. Degrades to a failed
    ParseResult (empty facts, manual-entry fallback) on any unrecoverable error."""
    try:
        text = extract_text(file_bytes, filename)
    except ValueError as exc:
        # Log the exception TYPE only — the message can embed the uploaded filename (which may carry
        # the candidate's name), and raw PII must never be logged (FR-218).
        log.info("resume text extraction failed (%s); manual entry required", type(exc).__name__)
        return ParseResult(parsed_facts=_empty_facts(), parse_status="failed", confidence="low")
    return structured_extract(text)
