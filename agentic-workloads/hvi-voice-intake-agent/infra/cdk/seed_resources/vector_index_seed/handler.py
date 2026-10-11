"""Vector-index seed custom-resource handler (Knowledge stack, always on).

Reuses ``tools/opensearch_admin.py`` verbatim (vendored beside this handler) —
no index/embedding logic is reimplemented in CDK. On create/update it:

1. ``create_indices()`` on the first run (builds both ``hvi-icd-concepts`` and
   ``hvi-call-transcripts``); and
2. the **safe** ``reload_concepts(health-kg.json)`` path — a delete-by-query
   scoped to ``hvi-icd-concepts`` only, then a bulk reload — so the
   ``hvi-call-transcripts`` audit index is never dropped and documents are never
   duplicated (exactly as the tool's docstring insists).

Surface forms embed with ``cohere.embed-multilingual-v3`` (dim 1024) at seed
time, so this handler needs Bedrock + the OpenSearch endpoint and the domain
must be Active (sequenced via ``add_dependency`` in the stack).

Fail-loud: the distractor **null class** must be present (the classifier's
safety logic depends on it), so the handler asserts a non-zero distractor count
and raises otherwise.

Invoked by the CDK ``Provider`` framework; the event carries ``RequestType``
and ``ResourceProperties`` (``SeedHash`` for change detection). The OpenSearch
endpoint + region + embed model arrive as env vars (``OPENSEARCH_ENDPOINT`` /
``DATA_REGION`` / ``EMBED_MODEL``), which ``opensearch_admin`` reads at import.
"""

from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_HEALTH_KG = os.path.join(_HERE, "health-kg.json")
_DISTRACTORS = os.path.join(_HERE, "distractors.json")


def _physical_id(event: dict) -> str:
    return event.get("PhysicalResourceId") or "hvi-vector-index-seed"


def _concepts_index_exists(opensearch_admin) -> bool:  # noqa: ANN001
    """True if the concepts index already exists (HEAD 200), else False (404)."""
    try:
        opensearch_admin.os_request("HEAD", f"/{opensearch_admin.IDX_CONCEPTS}")
        return True
    except RuntimeError:
        return False


def _distractor_count() -> int:
    with open(_DISTRACTORS, encoding="utf-8") as handle:
        doc = json.load(handle)
    return sum(len(grp.get("phrases", [])) for grp in doc.get("distractors", []))


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")

    if request_type == "Delete":
        # Indices (and the real transcript audit trail) are retained; the safe
        # reload is destructive only to the concepts index, never on delete.
        return {"PhysicalResourceId": _physical_id(event)}

    # Fail loud before touching the cluster if the null class is missing.
    if _distractor_count() == 0:
        raise RuntimeError(
            "vector seed aborted: distractor null class is empty "
            "(the classifier's not-a-condition safety path depends on it)"
        )

    # Imported here (not at module load) so a missing runtime dep surfaces as a
    # resource failure with a clear traceback rather than an import-time crash.
    import opensearch_admin  # vendored beside this handler

    # First run builds both indices; subsequent runs take the SAFE reload path
    # only (delete-by-query scoped to the concepts index), so the real
    # hvi-call-transcripts audit trail is never dropped.
    if not _concepts_index_exists(opensearch_admin):
        opensearch_admin.create_indices()

    opensearch_admin.reload_concepts(_HEALTH_KG)

    return {
        "PhysicalResourceId": _physical_id(event),
        "Data": {"Distractors": str(_distractor_count())},
    }
