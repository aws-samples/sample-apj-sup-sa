"""Submitter — normalize inputs, write manifests, submit to Batch."""
from .idempotency import filter_done, predict_output_key
from .s3_layout import (
    S3Layout,
    chunk_uris,
    make_submission_id,
    normalize_input_sources,
    parse_s3_uri,
)
from .secrets import upsert_hf_token
from .submit import SubmissionReport, submit_batch

__all__ = [
    "S3Layout",
    "SubmissionReport",
    "chunk_uris",
    "filter_done",
    "make_submission_id",
    "normalize_input_sources",
    "parse_s3_uri",
    "predict_output_key",
    "submit_batch",
    "upsert_hf_token",
]
