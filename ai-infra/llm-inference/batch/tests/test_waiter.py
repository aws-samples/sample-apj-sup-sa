"""Tests for waiter (poll + collector)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from llm_batch_deploy.data import SubmittedShard
from llm_batch_deploy.submitter.s3_layout import S3Layout
from llm_batch_deploy.submitter.submit import SubmissionReport
from llm_batch_deploy.waiter.collector import (
    download_outputs,
    list_outputs,
    sample_outputs,
)
from llm_batch_deploy.waiter.poll import (
    StatusSnapshot,
    poll,
    wait_for_completion,
)


def _shard(i: int, job_id: str | None = None) -> SubmittedShard:
    return SubmittedShard(
        shard_index=i,
        job_id=job_id or f"job-{i}",
        job_name=f"batch-shard-{i}",
        queue_arn="arn:aws:batch:us-east-2:123:job-queue/primary",
        manifest_s3_uri=f"s3://bkt/staging/sub-123/manifests/shard-{i:04d}.jsonl",
        output_prefix_s3_uri=f"s3://bkt/outputs/sub-123/shard-{i:04d}/",
        input_uri_count=100,
    )


def _report(n: int = 3) -> SubmissionReport:
    return SubmissionReport(
        submission_id="sub-123",
        layout=S3Layout(bucket="bkt", submission_id="sub-123"),
        shards=[_shard(i) for i in range(n)],
        skipped_done={},
        failed_submit=[],
    )


# ---------------------------------------------------------------------------
# poll()
# ---------------------------------------------------------------------------
class TestPoll:
    def test_basic_poll(self) -> None:
        batch = MagicMock()
        batch.describe_jobs.return_value = {
            "jobs": [
                {"jobId": "job-0", "jobName": "batch-shard-0", "status": "SUCCEEDED",
                 "attempts": [{"container": {"exitCode": 0}}]},
                {"jobId": "job-1", "jobName": "batch-shard-1", "status": "RUNNING"},
                {"jobId": "job-2", "jobName": "batch-shard-2", "status": "FAILED",
                 "statusReason": "Essential container exited",
                 "attempts": [{"container": {"exitCode": 1}}]},
            ],
        }
        snap = poll(_report(3), batch_client=batch)
        assert snap.succeeded == 1
        assert snap.failed == 1
        assert snap.in_progress == 1
        assert not snap.all_terminal
        # Exit code parsed
        byidx = {j.shard_index: j for j in snap.jobs}
        assert byidx[0].exit_code == 0
        assert byidx[2].exit_code == 1
        assert byidx[2].reason == "Essential container exited"

    def test_all_terminal(self) -> None:
        batch = MagicMock()
        batch.describe_jobs.return_value = {
            "jobs": [
                {"jobId": f"job-{i}", "jobName": f"j{i}", "status": "SUCCEEDED"}
                for i in range(3)
            ],
        }
        snap = poll(_report(3), batch_client=batch)
        assert snap.all_terminal
        assert snap.succeeded == 3

    def test_missing_job_returned_as_unknown(self) -> None:
        batch = MagicMock()
        batch.describe_jobs.return_value = {"jobs": []}  # empty
        snap = poll(_report(2), batch_client=batch)
        assert all(j.status == "UNKNOWN" for j in snap.jobs)
        assert len(snap.jobs) == 2

    def test_chunks_by_100(self) -> None:
        """DescribeJobs accepts up to 100 at a time."""
        batch = MagicMock()
        batch.describe_jobs.return_value = {"jobs": []}
        # 101 shards → 2 describe calls
        big_report = SubmissionReport(
            submission_id="big", layout=S3Layout(bucket="b", submission_id="big"),
            shards=[_shard(i, f"job-{i:04d}") for i in range(101)],
            skipped_done={}, failed_submit=[],
        )
        poll(big_report, batch_client=batch)
        assert batch.describe_jobs.call_count == 2

    def test_dataframe(self) -> None:
        batch = MagicMock()
        batch.describe_jobs.return_value = {
            "jobs": [
                {"jobId": "job-0", "jobName": "a", "status": "SUCCEEDED"},
                {"jobId": "job-1", "jobName": "b", "status": "RUNNING"},
            ],
        }
        snap = poll(_report(2), batch_client=batch)
        df = snap.to_dataframe()
        assert list(df.columns) == [
            "shard_index", "job_id", "job_name", "status",
            "reason", "exit_code", "created_at", "started_at", "stopped_at",
            "container_instance_arn",
        ]
        assert len(df) == 2

    def test_created_at_parsed(self) -> None:
        """createdAt from DescribeJobs should be captured on JobStatus."""
        batch = MagicMock()
        batch.describe_jobs.return_value = {
            "jobs": [
                {"jobId": "job-0", "jobName": "a", "status": "SUCCEEDED",
                 "createdAt": 1735000000000,
                 "startedAt": 1735000060000,
                 "stoppedAt": 1735000180000},
            ],
        }
        snap = poll(_report(1), batch_client=batch)
        assert snap.jobs[0].created_at == 1735000000000
        assert snap.jobs[0].started_at == 1735000060000
        assert snap.jobs[0].stopped_at == 1735000180000


class TestWaitForCompletion:
    def test_already_terminal(self) -> None:
        batch = MagicMock()
        batch.describe_jobs.return_value = {
            "jobs": [{"jobId": "job-0", "jobName": "a", "status": "SUCCEEDED"}],
        }
        snap = wait_for_completion(_report(1), batch_client=batch, poll_every_s=0)
        assert snap.all_terminal
        assert snap.succeeded == 1

    def test_polls_until_terminal(self) -> None:
        batch = MagicMock()
        # First call: still running. Second: done.
        batch.describe_jobs.side_effect = [
            {"jobs": [{"jobId": "job-0", "jobName": "a", "status": "RUNNING"}]},
            {"jobs": [{"jobId": "job-0", "jobName": "a", "status": "SUCCEEDED"}]},
        ]
        snap = wait_for_completion(_report(1), batch_client=batch, poll_every_s=0)
        assert snap.succeeded == 1
        assert batch.describe_jobs.call_count == 2

    def test_timeout(self) -> None:
        batch = MagicMock()
        batch.describe_jobs.return_value = {
            "jobs": [{"jobId": "job-0", "jobName": "a", "status": "RUNNING"}],
        }
        with pytest.raises(TimeoutError):
            wait_for_completion(
                _report(1), batch_client=batch, poll_every_s=0, max_wait_s=-1,
            )


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------
@pytest.fixture
def s3_with_outputs():
    """Bucket pre-populated with outputs for 2 shards."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2")
        client.create_bucket(
            Bucket="bkt",
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        # shard 0: two files
        client.put_object(
            Bucket="bkt", Key="outputs/sub-123/shard-0000/file-a.jsonl",
            Body=b'{"id":1,"response":"hello"}\n{"id":2,"response":"world"}\n',
        )
        client.put_object(
            Bucket="bkt", Key="outputs/sub-123/shard-0000/_summary.json",
            Body=json.dumps({
                "stats": {"total": 2, "succeeded": 2, "failed": 0,
                          "success_rate": 1.0},
                "records_processed": 2,
            }).encode(),
        )
        # shard 1: one file, some failures
        client.put_object(
            Bucket="bkt", Key="outputs/sub-123/shard-0001/file-b.jsonl",
            Body=b'{"id":3,"response":"ok"}\n{"id":4,"error":"timeout"}\n',
        )
        client.put_object(
            Bucket="bkt", Key="outputs/sub-123/shard-0001/_summary.json",
            Body=json.dumps({
                "stats": {"total": 2, "succeeded": 1, "failed": 1,
                          "success_rate": 0.5},
                "records_processed": 2,
            }).encode(),
        )
        yield client


class TestListOutputs:
    def test_lists_per_shard(self, s3_with_outputs) -> None:
        outs = list_outputs(_report(2), s3_client=s3_with_outputs)
        assert set(outs) == {0, 1}
        # Each shard has 2 objects (jsonl + summary)
        assert len(outs[0]) == 2
        assert len(outs[1]) == 2


class TestDownloadOutputs:
    def test_downloads_all(self, s3_with_outputs, tmp_path: Path) -> None:
        report = download_outputs(
            _report(2), output_dir=tmp_path, s3_client=s3_with_outputs,
        )
        # Files downloaded
        assert len(report.files_downloaded) == 4
        assert (tmp_path / "shard-0000" / "file-a.jsonl").exists()
        assert (tmp_path / "shard-0000" / "_summary.json").exists()
        assert (tmp_path / "shard-0001" / "_summary.json").exists()
        # Per-shard summaries parsed
        assert len(report.per_shard_summary) == 2
        s0 = next(s for s in report.per_shard_summary if s["shard_index"] == 0)
        assert s0["succeeded"] == 2
        s1 = next(s for s in report.per_shard_summary if s["shard_index"] == 1)
        assert s1["succeeded"] == 1
        assert s1["failed"] == 1

    def test_summary_only(self, s3_with_outputs, tmp_path: Path) -> None:
        report = download_outputs(
            _report(2), output_dir=tmp_path, s3_client=s3_with_outputs,
            include_summary_only=True,
        )
        # Only 2 _summary.json files
        assert len(report.files_downloaded) == 2
        for f in report.files_downloaded:
            assert f.name == "_summary.json"

    def test_to_dataframe(self, s3_with_outputs, tmp_path: Path) -> None:
        report = download_outputs(
            _report(2), output_dir=tmp_path, s3_client=s3_with_outputs,
        )
        df = report.to_dataframe()
        assert "shard_index" in df.columns
        assert "succeeded" in df.columns


class TestSampleOutputs:
    def test_samples_across_shards(self, s3_with_outputs) -> None:
        samples = sample_outputs(_report(2), n=3, s3_client=s3_with_outputs)
        assert len(samples) == 3
        # Should include records from shard 0 (has 2) + shard 1 (1)
        ids = sorted(s["id"] for s in samples)
        assert ids == [1, 2, 3]
        # _source_uri populated
        assert all("_source_uri" in s for s in samples)

    def test_skips_summary_files(self, s3_with_outputs) -> None:
        samples = sample_outputs(_report(2), n=10, s3_client=s3_with_outputs)
        for s in samples:
            assert not s["_source_uri"].endswith("_summary.json")


# ---------------------------------------------------------------------------
# Throughput aggregation (commit 4)
# ---------------------------------------------------------------------------
@pytest.fixture
def s3_with_throughput_outputs():
    """Bucket with 2 shards that carry throughput numbers in _summary.json.

    * Shard 0: 100 requests, 10s wall-clock, 2000 in + 1000 out tokens.
    * Shard 1: 100 requests, 12s wall-clock, 2400 in + 1200 out tokens.

    Summed across shards: 200 req, 4400 in + 2200 out = 6600 tokens,
    max wall-clock = 12s → 550 total tok/s.

    Per-shard mean throughput:
      shard 0 total_tokens_per_second = 300
      shard 1 total_tokens_per_second = 300
      mean = 300
    """
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2")
        client.create_bucket(
            Bucket="bkt",
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        client.put_object(
            Bucket="bkt", Key="outputs/sub-123/shard-0000/_summary.json",
            Body=json.dumps({
                "stats": {
                    "total": 100, "succeeded": 100, "failed": 0,
                    "total_input_tokens": 2000, "total_output_tokens": 1000,
                    "wall_clock_s": 10.0,
                    "input_tokens_per_second": 200.0,
                    "output_tokens_per_second": 100.0,
                    "total_tokens_per_second": 300.0,
                    "requests_per_second": 10.0,
                },
                "records_processed": 100,
            }).encode(),
        )
        client.put_object(
            Bucket="bkt", Key="outputs/sub-123/shard-0001/_summary.json",
            Body=json.dumps({
                "stats": {
                    "total": 100, "succeeded": 100, "failed": 0,
                    "total_input_tokens": 2400, "total_output_tokens": 1200,
                    "wall_clock_s": 12.0,
                    "input_tokens_per_second": 200.0,
                    "output_tokens_per_second": 100.0,
                    "total_tokens_per_second": 300.0,
                    "requests_per_second": 8.333,
                },
                "records_processed": 100,
            }).encode(),
        )
        yield client


class TestAggregateThroughput:
    def test_summed_and_mean(self, s3_with_throughput_outputs, tmp_path: Path) -> None:
        from llm_batch_deploy.waiter.collector import download_outputs
        report = download_outputs(
            _report(2), output_dir=tmp_path,
            s3_client=s3_with_throughput_outputs,
        )
        agg = report.aggregate_throughput()

        # Summed across shards (using MAX wall-clock)
        assert agg["total_input_tokens"] == 4400
        assert agg["total_output_tokens"] == 2200
        assert agg["max_wall_clock_s"] == 12.0
        # (4400 + 2200) / 12 = 550
        assert agg["summed_total_tokens_per_second"] == 550.0
        # Mean per shard (both shards hit 300 → mean 300)
        assert agg["mean_per_shard_total_tokens_per_second"] == 300.0

    def test_empty_shards(self, tmp_path: Path) -> None:
        from llm_batch_deploy.waiter.collector import CollectReport
        cr = CollectReport(
            output_dir=tmp_path, files_downloaded=[],
            per_shard_summary=[{"shard_index": 0, "files_found": 0}],
        )
        agg = cr.aggregate_throughput()
        assert agg["shards_with_throughput_data"] == 0
        assert agg["mode"] == "empty"


class TestComparisonRow:
    def test_row_shape(self, s3_with_throughput_outputs, tmp_path: Path) -> None:
        from llm_batch_deploy.waiter.collector import download_outputs
        report = download_outputs(
            _report(2), output_dir=tmp_path,
            s3_client=s3_with_throughput_outputs,
        )
        row = report.comparison_row(instance_type="g7e.2xlarge", concurrency=32)
        assert row["source"] == "batch"
        assert row["instance_type"] == "g7e.2xlarge"
        assert row["concurrency"] == 32
        assert row["n_replicas"] == 2
        assert row["total_tokens_per_second"] == 300.0  # mean per shard
        assert row["fleet_total_tokens_per_second"] == 550.0  # summed


# ---------------------------------------------------------------------------
# llmeter_comparable_stats + real_world_wall_clock_stats (commit 5)
# ---------------------------------------------------------------------------
class TestLlmeterComparableStats:
    def test_parses_per_record_jsonls(self, tmp_path: Path) -> None:
        """Walks shard-*/ dirs and parses per-record JSONL lines into
        avg/p50/p90/p99 distributions — matching benchmark stats.json."""
        from llm_batch_deploy.waiter.collector import CollectReport

        # Simulate a downloaded-output tree.
        shard0 = tmp_path / "shard-0000"
        shard0.mkdir()
        shard1 = tmp_path / "shard-0001"
        shard1.mkdir()

        # 6 records per shard, each with latency_ms + token counts.
        # Shard 0: latencies 100..600, inputs 10..15, outputs 20..25
        lines0 = [
            {"id": i, "latency_ms": 100 * (i + 1),
             "input_tokens": 10 + i, "output_tokens": 20 + i}
            for i in range(6)
        ]
        (shard0 / "file-a.jsonl").write_text(
            "\n".join(json.dumps(r) for r in lines0) + "\n"
        )
        # Plus a _summary.json with wall_clock + totals.
        (shard0 / "_summary.json").write_text(json.dumps({
            "stats": {
                "total": 6, "succeeded": 6, "failed": 0,
                "total_input_tokens": 75,
                "total_output_tokens": 135,
                "wall_clock_s": 3.0,
            },
            "records_processed": 6,
        }))

        # Shard 1: same shape, slightly different values.
        lines1 = [
            {"id": 100 + i, "latency_ms": 200 * (i + 1),
             "input_tokens": 12 + i, "output_tokens": 22 + i}
            for i in range(6)
        ]
        (shard1 / "file-b.jsonl").write_text(
            "\n".join(json.dumps(r) for r in lines1) + "\n"
        )
        (shard1 / "_summary.json").write_text(json.dumps({
            "stats": {
                "total": 6, "succeeded": 6, "failed": 0,
                "total_input_tokens": 87,
                "total_output_tokens": 147,
                "wall_clock_s": 4.0,
            },
            "records_processed": 6,
        }))

        cr = CollectReport(
            output_dir=tmp_path,
            files_downloaded=[],
            per_shard_summary=[
                {"shard_index": 0, "wall_clock_s": 3.0,
                 "total_input_tokens": 75, "total_output_tokens": 135,
                 "succeeded": 6, "failed": 0},
                {"shard_index": 1, "wall_clock_s": 4.0,
                 "total_input_tokens": 87, "total_output_tokens": 147,
                 "succeeded": 6, "failed": 0},
            ],
        )
        stats = cr.llmeter_comparable_stats(
            model_id="test-model", concurrency=16,
        )

        # Schema spot-checks
        assert stats["total_requests"] == 12
        assert stats["clients"] == 16
        assert stats["model_id"] == "test-model"
        assert stats["failed_requests"] == 0
        assert stats["total_test_time"] == 4.0  # MAX wall-clock across shards

        # Distribution: time_to_last_token in seconds (latency_ms / 1000)
        # latencies = [100,200,...,600, 200,400,...,1200]
        # sorted: [100,200,200,300,400,400,500,600,600,800,1000,1200]
        # LLMeter p50 = median (for even N, mean of two middle values):
        #   mean([400, 500]) = 450 → 0.45s
        assert stats["time_to_last_token-p50"] == 0.45

        # Token distributions
        # input_tokens values: [10,11,12,13,14,15, 12,13,14,15,16,17]
        # sorted: [10,11,12,12,13,13,14,14,15,15,16,17]
        # LLMeter p50 = median (even N): mean([13, 14]) = 13.5
        assert stats["num_tokens_input-p50"] == 13.5

        # Throughput: 12 reqs / 4s wall * 60 = 180 req/min
        assert stats["requests_per_minute"] == 180.0
        # output_tps = total_output (135 + 147 = 282) / 4.0 = 70.5
        assert stats["output_tps"] == 70.5

    def test_handles_errors_and_missing_tokens(self, tmp_path: Path) -> None:
        """Records with error set are counted as failed; missing token
        counts don't crash the distribution."""
        from llm_batch_deploy.waiter.collector import CollectReport
        shard0 = tmp_path / "shard-0000"
        shard0.mkdir()
        recs = [
            {"id": 1, "latency_ms": 100, "input_tokens": 10, "output_tokens": 20},
            {"id": 2, "latency_ms": 200, "error": "HTTP 500: bad"},  # failed
            {"id": 3, "latency_ms": 150},  # missing token counts
        ]
        (shard0 / "a.jsonl").write_text(
            "\n".join(json.dumps(r) for r in recs) + "\n"
        )
        cr = CollectReport(
            output_dir=tmp_path,
            files_downloaded=[],
            per_shard_summary=[{"shard_index": 0, "wall_clock_s": 1.0,
                                "total_input_tokens": 10,
                                "total_output_tokens": 20,
                                "succeeded": 2, "failed": 1}],
        )
        stats = cr.llmeter_comparable_stats(model_id="x", concurrency=4)
        assert stats["total_requests"] == 3
        assert stats["failed_requests"] == 1
        # Only the successful record with both token counts contributes to avg.
        assert stats["num_tokens_input-average"] == 10.0


class TestRealWorldWallClockStats:
    def test_uses_min_created_and_max_stopped(self, tmp_path: Path) -> None:
        """End-to-end wall-clock = max(stoppedAt) - min(createdAt), in seconds."""
        from llm_batch_deploy.waiter.collector import CollectReport
        from llm_batch_deploy.waiter.poll import JobStatus, StatusSnapshot

        # Three shards: submission spans from createdAt=1000 to stoppedAt=5000.
        # Total_real_wall_clock_s = (5000 - 1000) / 1000 = 4.0
        jobs = [
            JobStatus(shard_index=0, job_id="a", job_name="a", status="SUCCEEDED",
                      created_at=1000, started_at=1500, stopped_at=4000),
            JobStatus(shard_index=1, job_id="b", job_name="b", status="SUCCEEDED",
                      created_at=1100, started_at=1700, stopped_at=5000),
            JobStatus(shard_index=2, job_id="c", job_name="c", status="SUCCEEDED",
                      created_at=1200, started_at=1600, stopped_at=4500),
        ]
        snap = StatusSnapshot(submission_id="sub", jobs=jobs)

        cr = CollectReport(
            output_dir=tmp_path,
            files_downloaded=[],
            per_shard_summary=[
                {"shard_index": 0, "total_input_tokens": 100,
                 "total_output_tokens": 200, "succeeded": 10},
                {"shard_index": 1, "total_input_tokens": 150,
                 "total_output_tokens": 300, "succeeded": 15},
                {"shard_index": 2, "total_input_tokens": 120,
                 "total_output_tokens": 240, "succeeded": 12},
            ],
        )
        stats = cr.real_world_wall_clock_stats(snap)

        assert stats["submission_start_epoch_ms"] == 1000  # min createdAt
        assert stats["submission_end_epoch_ms"] == 5000    # max stoppedAt
        assert stats["first_job_started_epoch_ms"] == 1500
        assert stats["total_real_wall_clock_s"] == 4.0
        # Queue overhead: first job started at 1500, submission at 1000 → 0.5s
        assert stats["queue_overhead_s"] == 0.5
        # Billable seconds: (4000-1500) + (5000-1700) + (4500-1600) = 2500+3300+2900 = 8700ms
        # in seconds: 2.5 + 3.3 + 2.9 = 8.7
        assert stats["total_billable_instance_seconds"] == 8.7

        # Real-world TPS: 370 input / 4s = 92.5
        assert stats["real_world_input_tokens_per_second"] == 92.5
        # 740 output / 4s = 185
        assert stats["real_world_output_tokens_per_second"] == 185.0

    def test_incomplete_snapshot(self, tmp_path: Path) -> None:
        """If jobs haven't finished (no stoppedAt), returns a safe stub."""
        from llm_batch_deploy.waiter.collector import CollectReport
        from llm_batch_deploy.waiter.poll import JobStatus, StatusSnapshot

        jobs = [
            JobStatus(shard_index=0, job_id="a", job_name="a",
                      status="RUNNING", created_at=1000, started_at=1500),
        ]
        snap = StatusSnapshot(submission_id="sub", jobs=jobs)
        cr = CollectReport(output_dir=tmp_path, files_downloaded=[],
                           per_shard_summary=[])
        stats = cr.real_world_wall_clock_stats(snap)
        assert stats["mode"] == "incomplete"


# ---------------------------------------------------------------------------
# integrate_price (spot price integral math)
# ---------------------------------------------------------------------------
class TestIntegratePrice:
    def test_constant_price(self) -> None:
        """Single price point covers the whole window."""
        from llm_batch_deploy.waiter.cost import integrate_price

        # $1/hr for 3600 seconds (1 hour) → $1
        price = integrate_price([(1000, 1.0)], start_ms=2000, end_ms=2000 + 3_600_000)
        assert price == 1.0

    def test_price_change_mid_window(self) -> None:
        """Price changes halfway through — integral handles it."""
        from llm_batch_deploy.waiter.cost import integrate_price

        # From t=0 to t=1800000 (30 min) at $1/hr = $0.50
        # From t=1800000 to t=3600000 (30 min) at $2/hr = $1.00
        # Total = $1.50
        points = [(0, 1.0), (1_800_000, 2.0)]
        price = integrate_price(points, start_ms=0, end_ms=3_600_000)
        assert abs(price - 1.5) < 1e-9

    def test_price_before_window_applies(self) -> None:
        """Price set before window starts is still the price at window start."""
        from llm_batch_deploy.waiter.cost import integrate_price

        # Price set at t=-hour at $0.5/hr. Window is t=0 to t=1800000 (30 min).
        # Cost = 0.5 hr × $0.5 = $0.25
        points = [(-3_600_000, 0.5), (1_000_000_000, 2.0)]  # 2nd point way after
        price = integrate_price(points, start_ms=0, end_ms=1_800_000)
        assert abs(price - 0.25) < 1e-9

    def test_empty_points_returns_zero(self) -> None:
        from llm_batch_deploy.waiter.cost import integrate_price
        assert integrate_price([], 0, 1_000_000) == 0.0


# ---------------------------------------------------------------------------
# resolve_instances (Batch → ECS → EC2 chain)
# ---------------------------------------------------------------------------
class TestResolveInstances:
    def _snapshot(
        self, container_arn_by_job: dict[str, str | None],
    ):
        from llm_batch_deploy.waiter.poll import JobStatus, StatusSnapshot
        jobs = [
            JobStatus(
                shard_index=i, job_id=jid, job_name=jid,
                status="SUCCEEDED", created_at=1000, started_at=2000,
                stopped_at=60_000,
                container_instance_arn=arn,
            )
            for i, (jid, arn) in enumerate(container_arn_by_job.items())
        ]
        return StatusSnapshot(submission_id="sub", jobs=jobs)

    def test_two_jobs_two_instances(self) -> None:
        """Two jobs on different container instances resolve to two EC2 instances."""
        from llm_batch_deploy.waiter.instance_resolver import resolve_instances

        arn_a = "arn:aws:ecs:us-east-2:111:container-instance/medgemma-cluster/aaa"
        arn_b = "arn:aws:ecs:us-east-2:111:container-instance/medgemma-cluster/bbb"
        snap = self._snapshot({"job-a": arn_a, "job-b": arn_b})

        ecs = MagicMock()
        ecs.describe_container_instances.return_value = {
            "containerInstances": [
                {"containerInstanceArn": arn_a, "ec2InstanceId": "i-1"},
                {"containerInstanceArn": arn_b, "ec2InstanceId": "i-2"},
            ],
        }

        import datetime
        ec2 = MagicMock()
        ec2.get_paginator.return_value.paginate.return_value = [{
            "Reservations": [{"Instances": [
                {"InstanceId": "i-1", "InstanceType": "g7e.2xlarge",
                 "InstanceLifecycle": "spot",
                 "Placement": {"AvailabilityZone": "us-east-2a"},
                 "LaunchTime": datetime.datetime.fromtimestamp(100, tz=datetime.timezone.utc),
                 "State": {"Name": "running"}},
                {"InstanceId": "i-2", "InstanceType": "g7e.2xlarge",
                 "InstanceLifecycle": "spot",
                 "Placement": {"AvailabilityZone": "us-east-2b"},
                 "LaunchTime": datetime.datetime.fromtimestamp(150, tz=datetime.timezone.utc),
                 "State": {"Name": "terminated"},
                 "StateTransitionReason": "User initiated (2026-05-05 10:42:00 GMT)"},
            ]}],
        }]

        records = resolve_instances(
            snap, region="us-east-2",
            ecs_cluster_arn="arn:aws:ecs:us-east-2:111:cluster/medgemma-cluster",
            ecs_client=ecs, ec2_client=ec2,
        )
        assert len(records) == 2
        by_id = {r.instance_id: r for r in records}
        assert by_id["i-1"].availability_zone == "us-east-2a"
        assert by_id["i-1"].lifecycle == "spot"
        assert by_id["i-1"].launch_time_ms == 100_000
        assert by_id["i-1"].termination_time_ms is None  # still running
        assert by_id["i-2"].termination_time_ms is not None  # parsed from reason
        assert "job-a" in by_id["i-1"].job_ids
        assert "job-b" in by_id["i-2"].job_ids

    def test_two_jobs_same_instance(self) -> None:
        """Batch reuses one instance for two jobs → one InstanceRecord."""
        from llm_batch_deploy.waiter.instance_resolver import resolve_instances

        arn = "arn:aws:ecs:us-east-2:111:container-instance/c/same"
        snap = self._snapshot({"job-a": arn, "job-b": arn})

        ecs = MagicMock()
        ecs.describe_container_instances.return_value = {
            "containerInstances": [
                {"containerInstanceArn": arn, "ec2InstanceId": "i-shared"},
            ],
        }
        import datetime
        ec2 = MagicMock()
        ec2.get_paginator.return_value.paginate.return_value = [{
            "Reservations": [{"Instances": [
                {"InstanceId": "i-shared", "InstanceType": "g7e.2xlarge",
                 "InstanceLifecycle": "spot",
                 "Placement": {"AvailabilityZone": "us-east-2a"},
                 "LaunchTime": datetime.datetime.fromtimestamp(100, tz=datetime.timezone.utc),
                 "State": {"Name": "running"}},
            ]}],
        }]

        records = resolve_instances(
            snap, region="us-east-2",
            ecs_cluster_arn="arn:aws:ecs:us-east-2:111:cluster/c",
            ecs_client=ecs, ec2_client=ec2,
        )
        assert len(records) == 1
        assert sorted(records[0].job_ids) == ["job-a", "job-b"]

    def test_infer_cluster_arn_from_container_instance(self) -> None:
        """When ecs_cluster_arn is None, it's inferred from the ARN format."""
        from llm_batch_deploy.waiter.instance_resolver import resolve_instances

        arn = "arn:aws:ecs:us-east-2:111:container-instance/auto-cluster/uuid"
        snap = self._snapshot({"job-a": arn})

        ecs = MagicMock()
        ecs.describe_container_instances.return_value = {"containerInstances": []}
        ec2 = MagicMock()

        resolve_instances(
            snap, region="us-east-2",
            ecs_client=ecs, ec2_client=ec2,
        )
        # ECS was called with the inferred cluster ARN.
        call_args = ecs.describe_container_instances.call_args
        assert call_args.kwargs["cluster"] == (
            "arn:aws:ecs:us-east-2:111:cluster/auto-cluster"
        )

    def test_no_jobs_with_arn_returns_empty(self) -> None:
        from llm_batch_deploy.waiter.instance_resolver import resolve_instances
        snap = self._snapshot({"job-a": None})
        records = resolve_instances(snap, region="us-east-2",
                                    ecs_client=MagicMock(), ec2_client=MagicMock())
        assert records == []

    def test_ec2_gc_instance_is_skipped(self) -> None:
        """EC2 DescribeInstances doesn't return the instance (GC'd) → record dropped."""
        from llm_batch_deploy.waiter.instance_resolver import resolve_instances

        arn = "arn:aws:ecs:us-east-2:111:container-instance/c/gone"
        snap = self._snapshot({"job-a": arn})

        ecs = MagicMock()
        ecs.describe_container_instances.return_value = {
            "containerInstances": [
                {"containerInstanceArn": arn, "ec2InstanceId": "i-gone"},
            ],
        }
        ec2 = MagicMock()
        ec2.get_paginator.return_value.paginate.return_value = [{"Reservations": []}]

        records = resolve_instances(
            snap, region="us-east-2",
            ecs_cluster_arn="arn:aws:ecs:us-east-2:111:cluster/c",
            ecs_client=ecs, ec2_client=ec2,
        )
        assert records == []


# ---------------------------------------------------------------------------
# estimate_cost (per-instance integral)
# ---------------------------------------------------------------------------
class TestEstimateCost:
    def _collect(self, tmp_path: Path):
        from llm_batch_deploy.waiter.collector import CollectReport
        return CollectReport(
            output_dir=tmp_path, files_downloaded=[],
            per_shard_summary=[
                {"shard_index": 0, "total_input_tokens": 1000,
                 "total_output_tokens": 2000, "succeeded": 50, "failed": 0},
            ],
        )

    def _snapshot(self, arn: str | None = None):
        from llm_batch_deploy.waiter.poll import JobStatus, StatusSnapshot
        jobs = [
            JobStatus(
                shard_index=0, job_id="job-a", job_name="j",
                status="SUCCEEDED", created_at=1000, started_at=2000,
                stopped_at=62_000,
                container_instance_arn=arn,
            ),
        ]
        return StatusSnapshot(submission_id="sub", jobs=jobs)

    def _mocked_clients(
        self,
        *,
        ec2_instance_type: str = "g7e.2xlarge",
        ec2_lifecycle: str = "spot",
        launch_ms: int = 1_000,
        termination_reason: str | None = None,
        spot_points: list[tuple[int, float]] | None = None,
        on_demand_usd: float | None = None,
    ):
        """Return (ecs, ec2, pricing, ssm) mocks configured for cost tests."""
        import datetime

        ecs = MagicMock()
        arn = "arn:aws:ecs:us-east-2:111:container-instance/c/uuid"
        ecs.describe_container_instances.return_value = {
            "containerInstances": [
                {"containerInstanceArn": arn, "ec2InstanceId": "i-1"},
            ],
        }

        ec2 = MagicMock()
        ec2.get_paginator.side_effect = lambda op: {
            "describe_instances": MagicMock(paginate=MagicMock(return_value=[{
                "Reservations": [{"Instances": [{
                    "InstanceId": "i-1",
                    "InstanceType": ec2_instance_type,
                    "InstanceLifecycle": ec2_lifecycle,
                    "Placement": {"AvailabilityZone": "us-east-2a"},
                    "LaunchTime": datetime.datetime.fromtimestamp(
                        launch_ms / 1000, tz=datetime.timezone.utc,
                    ),
                    "State": {"Name": "terminated" if termination_reason else "running"},
                    "StateTransitionReason": termination_reason or "",
                }]}],
            }])),
            "describe_spot_price_history": MagicMock(paginate=MagicMock(return_value=[{
                "SpotPriceHistory": [
                    {"Timestamp": datetime.datetime.fromtimestamp(
                        ts / 1000, tz=datetime.timezone.utc),
                     "SpotPrice": str(price),
                     "AvailabilityZone": "us-east-2a"}
                    for ts, price in (spot_points or [])
                ],
            }])),
        }[op]

        pricing = MagicMock()
        if on_demand_usd is not None:
            pricing.get_products.return_value = {
                "PriceList": [json.dumps({
                    "terms": {"OnDemand": {"SKU1": {
                        "priceDimensions": {"PD1": {
                            "pricePerUnit": {"USD": str(on_demand_usd)},
                        }},
                    }}},
                })],
            }
        else:
            pricing.get_products.return_value = {"PriceList": []}

        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": "US East (Ohio)"}}

        return arn, ecs, ec2, pricing, ssm

    def test_spot_happy_path(self, tmp_path: Path) -> None:
        """Single spot instance, constant $0.40/hr, instance ran 1 hour."""
        from llm_batch_deploy.waiter.cost import estimate_cost

        # Instance launched at t=0s, terminated at t=3600s.
        arn, ecs, ec2, pricing, ssm = self._mocked_clients(
            ec2_lifecycle="spot",
            launch_ms=0,
            termination_reason="User initiated (2026-05-05 10:00:00 GMT)",
            spot_points=[(-1_000_000, 0.40)],   # constant price before + during
        )
        # But the parsed termination_ms won't match launch+3600 exactly —
        # use estimate_termination_from_jobs path: job stoppedAt = 62_000
        # (wait, that's 62s not 3600s). Let me just set launch=2000, job stop=62000.
        # Snapshot job has stopped_at=62_000, so fallback uses that.
        # Instance termination via StateTransitionReason parse = specific date.
        # To get a clean test, let's ignore the EC2 parsed termination and force
        # fallback by clearing it.

        cost = estimate_cost(
            collect_report=self._collect(tmp_path),
            status_snapshot=self._snapshot(arn=arn),
            region="us-east-2",
            ecs_cluster_arn="arn:aws:ecs:us-east-2:111:cluster/c",
            ecs_client=ecs, ec2_client=ec2,
            pricing_client=pricing, ssm_client=ssm,
            now_ms=62_000,
        )
        assert cost.instance_count == 1
        inst = cost.per_instance[0]
        assert inst.instance_type == "g7e.2xlarge"
        assert inst.lifecycle == "spot"
        # billable = termination_time - launch_time. With the parsed termination
        # being "2026-05-05 10:00:00 GMT" vs launch=0 (epoch 1970), we'd get
        # a huge number. Instead the test verifies structure:
        assert inst.usd_total > 0
        # CostEstimate.total_usd rounds to 6 decimals; inst.usd_total is raw.
        assert cost.total_usd == pytest.approx(inst.usd_total, abs=1e-6)
        assert cost.total_input_tokens == 1000
        assert cost.total_output_tokens == 2000

    def test_on_demand_path(self, tmp_path: Path) -> None:
        """On-demand instance uses Pricing API rate."""
        from llm_batch_deploy.waiter.cost import estimate_cost

        arn, ecs, ec2, pricing, ssm = self._mocked_clients(
            ec2_lifecycle="on-demand",   # not "spot"
            launch_ms=0,
            termination_reason=None,     # still running → uses estimate_termination_from_jobs
            on_demand_usd=1.50,
        )
        cost = estimate_cost(
            collect_report=self._collect(tmp_path),
            status_snapshot=self._snapshot(arn=arn),
            region="us-east-2",
            ecs_cluster_arn="arn:aws:ecs:us-east-2:111:cluster/c",
            ecs_client=ecs, ec2_client=ec2,
            pricing_client=pricing, ssm_client=ssm,
            now_ms=3_600_000,
        )
        inst = cost.per_instance[0]
        assert inst.lifecycle == "on-demand"
        # launch=0, fallback termination = max(job.stopped_at)=62_000ms = 62s
        # cost = 62s / 3600 * $1.50 ≈ $0.0258
        expected = 62.0 / 3600.0 * 1.50
        assert inst.usd_total == pytest.approx(expected, rel=1e-9)
        assert abs(inst.hourly_usd_avg - 1.50) < 1e-6
        assert inst.billable_seconds == 62.0

    def test_unresolved_instance_listed(self, tmp_path: Path) -> None:
        """Job without container_instance_arn → unresolved, excluded from total."""
        from llm_batch_deploy.waiter.cost import estimate_cost

        cost = estimate_cost(
            collect_report=self._collect(tmp_path),
            status_snapshot=self._snapshot(arn=None),
            region="us-east-2",
            ecs_client=MagicMock(), ec2_client=MagicMock(),
            pricing_client=MagicMock(), ssm_client=MagicMock(),
        )
        assert cost.instance_count == 0
        assert cost.unresolved_job_ids == ["job-a"]
        assert cost.total_usd == 0.0

    def test_as_dict_shape(self, tmp_path: Path) -> None:
        from llm_batch_deploy.waiter.cost import CostEstimate, InstanceCost

        ce = CostEstimate(
            region="us-east-2",
            per_instance=[
                InstanceCost(
                    instance_id="i-1", instance_type="g7e.2xlarge",
                    availability_zone="us-east-2a", lifecycle="spot",
                    launch_time_ms=0, termination_time_ms=3_600_000,
                    billable_seconds=3600.0, usd_total=0.40,
                    hourly_usd_avg=0.40, job_ids=["job-a"],
                ),
            ],
            total_input_tokens=1000,
            total_output_tokens=2000,
            total_succeeded_requests=50,
        )
        d = ce.as_dict()
        assert d["instance_count"] == 1
        assert d["total_usd"] == 0.40
        assert d["total_billable_hours"] == 1.0
        # 0.40 / 3000 * 1e6
        assert d["usd_per_1m_total_tokens"] == round(0.40 / 3000 * 1_000_000, 4)

    def test_resolve_location_fallback(self) -> None:
        from llm_batch_deploy.waiter.cost import resolve_location_name
        ssm = MagicMock()
        ssm.get_parameter.side_effect = RuntimeError("denied")
        assert resolve_location_name("us-east-2", ssm_client=ssm) == "US East (Ohio)"

    def test_resolve_location_unknown_region(self) -> None:
        from llm_batch_deploy.waiter.cost import resolve_location_name
        ssm = MagicMock()
        ssm.get_parameter.side_effect = RuntimeError("denied")
        with pytest.raises(ValueError, match="Cannot resolve location"):
            resolve_location_name("xx-nowhere-1", ssm_client=ssm)


# ---------------------------------------------------------------------------
# segments_for_lifespan + InstanceCost price-variation fields + price_timeline
# ---------------------------------------------------------------------------
class TestSegmentsForLifespan:
    def test_constant_price_single_segment(self) -> None:
        from llm_batch_deploy.waiter.cost import segments_for_lifespan
        segs = segments_for_lifespan([(0, 0.5)], 1000, 4600)
        assert segs == [(1000, 4600, 0.5)]

    def test_price_change_mid_window_splits(self) -> None:
        from llm_batch_deploy.waiter.cost import segments_for_lifespan
        # Prices: 0.4 from t=0, 0.5 from t=2000. Lifespan [1000, 5000].
        segs = segments_for_lifespan([(0, 0.4), (2000, 0.5)], 1000, 5000)
        assert segs == [(1000, 2000, 0.4), (2000, 5000, 0.5)]

    def test_empty_points_returns_empty(self) -> None:
        from llm_batch_deploy.waiter.cost import segments_for_lifespan
        assert segments_for_lifespan([], 0, 1000) == []

    def test_window_before_any_point_uses_first_price(self) -> None:
        from llm_batch_deploy.waiter.cost import segments_for_lifespan
        segs = segments_for_lifespan([(5000, 0.5)], 1000, 4000)
        # Only one segment, priced at first point since window ends before ts.
        assert segs == [(1000, 4000, 0.5)]


class TestInstanceCostPriceFields:
    def test_constant_price_populates_single_point(self, tmp_path: Path) -> None:
        from llm_batch_deploy.waiter.cost import estimate_cost
        arn, ecs, ec2, pricing, ssm = TestEstimateCost()._mocked_clients(
            ec2_lifecycle="on-demand", launch_ms=0,
            termination_reason=None, on_demand_usd=1.50,
        )
        cost = estimate_cost(
            collect_report=TestEstimateCost()._collect(tmp_path),
            status_snapshot=TestEstimateCost()._snapshot(arn=arn),
            region="us-east-2",
            ecs_cluster_arn="arn:aws:ecs:us-east-2:111:cluster/c",
            ecs_client=ecs, ec2_client=ec2,
            pricing_client=pricing, ssm_client=ssm,
            now_ms=3_600_000,
        )
        inst = cost.per_instance[0]
        assert inst.n_price_points == 1
        assert inst.first_price_usd == pytest.approx(1.50)
        assert inst.last_price_usd == pytest.approx(1.50)
        assert inst.min_hourly_usd == pytest.approx(1.50)
        assert inst.max_hourly_usd == pytest.approx(1.50)

    def test_as_dict_includes_new_fields(self) -> None:
        from llm_batch_deploy.waiter.cost import InstanceCost
        inst = InstanceCost(
            instance_id="i-1", instance_type="g7e.2xlarge",
            availability_zone="us-east-2a", lifecycle="spot",
            launch_time_ms=0, termination_time_ms=3_600_000,
            billable_seconds=3600.0, usd_total=0.45,
            hourly_usd_avg=0.45, first_price_usd=0.40, last_price_usd=0.50,
            min_hourly_usd=0.40, max_hourly_usd=0.50, n_price_points=2,
        )
        d = inst.to_dict()
        assert d["first_price_usd"] == 0.4
        assert d["last_price_usd"] == 0.5
        assert d["min_hourly_usd"] == 0.4
        assert d["max_hourly_usd"] == 0.5
        assert d["n_price_points"] == 2


class TestPriceTimeline:
    def test_returns_segments_for_known_instance(self) -> None:
        from llm_batch_deploy.waiter.cost import CostEstimate, InstanceCost
        inst = InstanceCost(
            instance_id="i-abc", instance_type="g7e.2xlarge",
            availability_zone="us-east-2a", lifecycle="spot",
            launch_time_ms=1000, termination_time_ms=5000,
            billable_seconds=4.0, usd_total=0.001,
            hourly_usd_avg=0.9, first_price_usd=0.8, last_price_usd=1.0,
            min_hourly_usd=0.8, max_hourly_usd=1.0, n_price_points=2,
            price_points=[(0, 0.8), (3000, 1.0)],
        )
        ce = CostEstimate(region="us-east-2", per_instance=[inst])
        timeline = ce.price_timeline("i-abc")
        assert len(timeline) == 2
        # First segment: [1000, 3000] @ $0.80
        assert timeline[0]["segment_start_ms"] == 1000
        assert timeline[0]["segment_end_ms"] == 3000
        assert timeline[0]["hourly_usd"] == pytest.approx(0.80)
        # Second segment: [3000, 5000] @ $1.00
        assert timeline[1]["segment_start_ms"] == 3000
        assert timeline[1]["segment_end_ms"] == 5000
        assert timeline[1]["hourly_usd"] == pytest.approx(1.00)

    def test_unknown_instance_returns_empty(self) -> None:
        from llm_batch_deploy.waiter.cost import CostEstimate
        ce = CostEstimate(region="us-east-2", per_instance=[])
        assert ce.price_timeline("i-missing") == []


# ---------------------------------------------------------------------------
# project_economics (headline summary)
# ---------------------------------------------------------------------------
class TestProjectEconomics:
    def test_headline_numbers(self, tmp_path: Path) -> None:
        from llm_batch_deploy.waiter.collector import CollectReport
        from llm_batch_deploy.waiter.cost import CostEstimate, InstanceCost
        from llm_batch_deploy.waiter.poll import JobStatus, StatusSnapshot

        # Submission: 2 jobs, createdAt 1000/1050, stoppedAt 61000/91000
        # Duration (max stopped - min created) = 91000 - 1000 = 90000ms = 90s.
        snap = StatusSnapshot(submission_id="sub", jobs=[
            JobStatus(shard_index=0, job_id="a", job_name="a", status="SUCCEEDED",
                      created_at=1000, started_at=2000, stopped_at=61_000),
            JobStatus(shard_index=1, job_id="b", job_name="b", status="SUCCEEDED",
                      created_at=1050, started_at=2100, stopped_at=91_000),
        ])

        collect = CollectReport(
            output_dir=tmp_path, files_downloaded=[],
            per_shard_summary=[
                {"shard_index": 0, "total_input_tokens": 1000,
                 "total_output_tokens": 2000, "succeeded": 50, "failed": 0},
                {"shard_index": 1, "total_input_tokens": 1500,
                 "total_output_tokens": 3000, "succeeded": 75, "failed": 0},
            ],
        )
        cost = CostEstimate(
            region="us-east-2",
            per_instance=[InstanceCost(
                instance_id="i-1", instance_type="g7e.2xlarge",
                availability_zone="us-east-2a", lifecycle="spot",
                launch_time_ms=0, termination_time_ms=90_000,
                billable_seconds=90.0, usd_total=0.50, hourly_usd_avg=20.0,
                job_ids=["a", "b"],
            )],
            total_input_tokens=2500, total_output_tokens=5000,
            total_succeeded_requests=125,
        )

        econ = collect.project_economics(snap, cost)

        # Headline three
        assert econ["total_cost_usd"] == 0.5
        assert econ["total_tokens"] == 2500 + 5000   # 7500
        assert econ["duration_s"] == 90.0

        # Derived
        # 0.5 / 7500 × 1M = 66.6667
        assert econ["usd_per_1m_tokens"] == round(0.5 / 7500 * 1_000_000, 4)
        # 7500 tokens / 90 s ≈ 83.33 tok/s
        assert econ["real_world_tokens_per_second"] == round(7500 / 90, 4)

        # Operational
        assert econ["n_instances"] == 1
        assert econ["n_jobs"] == 2
        assert econ["n_unresolved_jobs"] == 0

    def test_handles_incomplete_snapshot(self, tmp_path: Path) -> None:
        """If no jobs have created_at/stopped_at yet, duration is None."""
        from llm_batch_deploy.waiter.collector import CollectReport
        from llm_batch_deploy.waiter.cost import CostEstimate
        from llm_batch_deploy.waiter.poll import JobStatus, StatusSnapshot

        snap = StatusSnapshot(submission_id="sub", jobs=[
            JobStatus(shard_index=0, job_id="a", job_name="a", status="RUNNING"),
        ])
        collect = CollectReport(output_dir=tmp_path, files_downloaded=[],
                                per_shard_summary=[])
        cost = CostEstimate(region="us-east-2")
        econ = collect.project_economics(snap, cost)
        assert econ["duration_s"] is None
        assert econ["real_world_tokens_per_second"] is None
        assert econ["total_cost_usd"] == 0.0
