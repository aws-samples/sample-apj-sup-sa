"""Container entrypoint — what Batch runs.

Reads env vars set by the job definition + SubmitJob overrides:

    MANIFEST_S3_URI      s3://bucket/staging/<job>/manifest.jsonl
    OUTPUT_PREFIX_S3_URI s3://bucket/out/<job>/
    VLLM_BASE_URL        http://localhost:8000  (vLLM runs as sibling process)
    IN_FLIGHT_PER_JOB    32
    OVERWRITE            false
    MODEL_ID             served_model_name — used as the 'model' field if
                         a request doesn't specify one.

The container itself also starts vLLM (see Dockerfile CMD) before invoking
this entrypoint.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .s3_io import S3Uri, iter_input_records, object_exists, read_text, s3_session, write_text
from .vllm_driver import DriverStats, InferenceResult, drive_inference, wait_for_vllm_ready

LOG = logging.getLogger("llm_batch_deploy.runtime.entrypoint")


async def process_shard(
    *,
    manifest_uri: str,
    output_prefix_uri: str,
    vllm_base_url: str,
    in_flight: int,
    model_id: str,
    overwrite: bool,
    vllm_startup_timeout_s: float = 900.0,
    request_timeout_s: float = 120.0,
) -> int:
    """Main async entrypoint. Returns an exit code (0 = OK)."""

    # 1. Wait for vLLM to come up in the sibling process.
    LOG.info("Waiting for vLLM to become ready at %s …", vllm_base_url)
    await wait_for_vllm_ready(vllm_base_url, timeout_s=vllm_startup_timeout_s)

    # 2. Fetch the manifest.
    manifest = S3Uri.parse(manifest_uri)
    output_prefix = S3Uri.parse(output_prefix_uri)

    session = s3_session()
    async with session.client("s3") as s3:
        manifest_body = await read_text(s3, manifest)
        input_uris = [
            line.strip() for line in manifest_body.splitlines() if line.strip()
        ]
        LOG.info("Manifest: %d input URIs to process", len(input_uris))

        # 3. Idempotency: filter out inputs whose output already exists.
        if not overwrite:
            existing = await _filter_already_done(s3, input_uris, output_prefix)
            if existing:
                LOG.info(
                    "Skipping %d/%d inputs whose output already exists (overwrite=False).",
                    len(existing), len(input_uris),
                )
            input_uris = [u for u in input_uris if u not in existing]

        if not input_uris:
            LOG.warning("Nothing to do after idempotency filter. Exiting 0.")
            return 0

        # 4. Fetch input bodies concurrently + flatten to records.
        records_per_input = await _fetch_input_records(s3, input_uris, model_id)

        all_records: list[tuple[str, dict]] = []
        for uri, records in records_per_input:
            for i, rec in enumerate(records):
                key = rec.get("id") if isinstance(rec.get("id"), (str, int)) else f"{uri}#{i}"
                all_records.append((str(key), rec))
        LOG.info(
            "Flattened to %d total records across %d input files.",
            len(all_records), len(records_per_input),
        )

        if not all_records:
            LOG.warning("No records after expansion. Exiting 0.")
            return 0

        # 5. Run the inference loop.
        results, stats = await drive_inference(
            all_records,
            vllm_base_url=vllm_base_url,
            in_flight=in_flight,
            request_timeout_s=request_timeout_s,
        )
        LOG.info("Driver done: %s", stats.as_dict())

        # 6. Write one output JSONL per input URI (matching shape).
        await _write_outputs(s3, output_prefix, records_per_input, results)

        # 7. Write a summary.
        import os as _os
        summary_uri = output_prefix.join("_summary.json")
        summary = {
            "stats": stats.as_dict(),
            "input_uri_count": len(input_uris),
            "records_processed": len(all_records),
            "model_id": model_id,
            "in_flight_per_job": in_flight,
            "submission_shard_index": _os.environ.get("SUBMISSION_SHARD_INDEX"),
        }
        await write_text(s3, summary_uri, json.dumps(summary, indent=2))
        LOG.info("Summary → %s", summary_uri)

    # 8. Exit code: 0 if at least one prompt succeeded.
    if stats.succeeded == 0 and stats.total > 0:
        LOG.error("All %d records failed. Exiting non-zero.", stats.total)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _output_key_for(output_prefix: S3Uri, input_uri: str) -> S3Uri:
    """Mirror input filename under output_prefix/.

    ``s3://bucket/inputs/foo.jsonl`` + output prefix
    ``s3://out/job-123/`` → ``s3://out/job-123/foo.jsonl``.
    """
    filename = input_uri.rsplit("/", 1)[-1]
    return output_prefix.join(filename)


async def _filter_already_done(
    s3, input_uris: list[str], output_prefix: S3Uri,
) -> set[str]:
    """Returns URIs whose output key already exists."""
    async def _check(uri: str) -> str | None:
        exists = await object_exists(s3, _output_key_for(output_prefix, uri))
        return uri if exists else None

    # Bounded concurrency for HEAD calls
    sem = asyncio.Semaphore(32)
    async def _guarded(uri: str) -> str | None:
        async with sem:
            return await _check(uri)

    flagged = await asyncio.gather(*(_guarded(u) for u in input_uris))
    return {u for u in flagged if u is not None}


async def _fetch_input_records(
    s3, input_uris: list[str], model_id: str,
) -> list[tuple[str, list[dict]]]:
    """Fetch all input bodies concurrently; return [(uri, records), ...].

    Injects ``model_id`` if a record doesn't specify one.
    """
    sem = asyncio.Semaphore(32)

    async def _one(uri: str) -> tuple[str, list[dict]]:
        async with sem:
            body = await read_text(s3, S3Uri.parse(uri))
        records = iter_input_records(body, uri=uri)
        for r in records:
            r.setdefault("model", model_id)
        return uri, records

    return list(await asyncio.gather(*(_one(u) for u in input_uris)))


async def _write_outputs(
    s3,
    output_prefix: S3Uri,
    records_per_input: list[tuple[str, list[dict]]],
    results: list[InferenceResult],
) -> None:
    """Write one output JSONL per input URI, matching the input's shape.

    Outputs are grouped back by their originating input file via
    ``input_key`` prefix matching.
    """
    # Index results by input_key
    by_key = {r.input_key: r for r in results}

    async def _one(uri: str, records: list[dict]) -> None:
        lines: list[str] = []
        for i, rec in enumerate(records):
            explicit_id = rec.get("id") if isinstance(rec.get("id"), (str, int)) else None
            key = str(explicit_id) if explicit_id is not None else f"{uri}#{i}"
            result = by_key.get(key)
            if result is None:
                # Shouldn't happen, but don't drop records silently.
                lines.append(json.dumps({
                    "id": explicit_id, "input_key": key, "request": rec,
                    "response": None, "error": "no result produced",
                }, ensure_ascii=False))
            else:
                lines.append(result.to_jsonl_line())
        out_uri = _output_key_for(output_prefix, uri)
        body = "\n".join(lines) + "\n"
        await write_text(s3, out_uri, body, content_type="application/x-ndjson")

    await asyncio.gather(*(_one(u, r) for u, r in records_per_input))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    """Called by the Docker container's CMD (after starting vLLM)."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(prog="llm-batch-runtime")
    parser.add_argument("--manifest-s3-uri", default=os.environ.get("MANIFEST_S3_URI"))
    parser.add_argument("--output-prefix-s3-uri", default=os.environ.get("OUTPUT_PREFIX_S3_URI"))
    parser.add_argument("--vllm-base-url", default=os.environ.get("VLLM_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--in-flight", type=int, default=int(os.environ.get("IN_FLIGHT_PER_JOB", "32")))
    parser.add_argument("--model-id", default=os.environ.get("MODEL_ID"))
    parser.add_argument("--overwrite", action="store_true",
                        default=os.environ.get("OVERWRITE", "false").lower() == "true")
    parser.add_argument("--vllm-startup-timeout-s", type=float,
                        default=float(os.environ.get("VLLM_STARTUP_TIMEOUT_S", "900")))
    parser.add_argument("--request-timeout-s", type=float,
                        default=float(os.environ.get("REQUEST_TIMEOUT_S", "120")))
    args = parser.parse_args()

    missing = [
        name for name, val in (
            ("MANIFEST_S3_URI / --manifest-s3-uri", args.manifest_s3_uri),
            ("OUTPUT_PREFIX_S3_URI / --output-prefix-s3-uri", args.output_prefix_s3_uri),
            ("MODEL_ID / --model-id", args.model_id),
        ) if not val
    ]
    if missing:
        print(f"Missing required: {', '.join(missing)}", file=sys.stderr)
        return 2

    return asyncio.run(process_shard(
        manifest_uri=args.manifest_s3_uri,
        output_prefix_uri=args.output_prefix_s3_uri,
        vllm_base_url=args.vllm_base_url,
        in_flight=args.in_flight,
        model_id=args.model_id,
        overwrite=args.overwrite,
        vllm_startup_timeout_s=args.vllm_startup_timeout_s,
        request_timeout_s=args.request_timeout_s,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
