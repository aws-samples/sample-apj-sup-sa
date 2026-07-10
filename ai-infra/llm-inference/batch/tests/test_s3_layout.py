"""Tests for S3 layout helpers + idempotency (moto-backed)."""
from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from llm_batch_deploy.submitter.idempotency import (
    filter_done,
    predict_output_key,
)
from llm_batch_deploy.submitter.s3_layout import (
    S3Layout,
    chunk_uris,
    make_submission_id,
    normalize_input_sources,
    parse_s3_uri,
    read_manifest,
    write_manifest,
)


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2")
        client.create_bucket(
            Bucket="test-bucket",
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        yield client


class TestSubmissionId:
    def test_format(self) -> None:
        sid = make_submission_id()
        # YYYYMMDDTHHmmSSZ-<8 hex>
        assert len(sid) == 16 + 1 + 8
        assert "T" in sid and sid[-9] == "-"

    def test_with_prefix(self) -> None:
        sid = make_submission_id("foo")
        assert sid.startswith("foo-")


class TestS3Layout:
    def test_prefixes(self) -> None:
        L = S3Layout(bucket="b", submission_id="sub-1")
        assert L.manifest_prefix == "staging/sub-1/manifests/"
        assert L.inputs_prefix == "staging/sub-1/inputs/"
        assert L.outputs_prefix == "outputs/sub-1/"

    def test_uris(self) -> None:
        L = S3Layout(bucket="b", submission_id="s")
        assert L.manifest_uri(0) == "s3://b/staging/s/manifests/shard-0000.jsonl"
        assert L.output_prefix_uri(7) == "s3://b/outputs/s/shard-0007/"
        assert L.upload_uri("foo.jsonl") == "s3://b/staging/s/inputs/foo.jsonl"


class TestParseS3Uri:
    def test_happy(self) -> None:
        b, k = parse_s3_uri("s3://bucket/path/to/thing.json")
        assert b == "bucket"
        assert k == "path/to/thing.json"

    def test_rejects_non_s3(self) -> None:
        with pytest.raises(ValueError):
            parse_s3_uri("http://example.com/x")

    def test_rejects_missing_bucket(self) -> None:
        with pytest.raises(ValueError):
            parse_s3_uri("s3:///key-only")


class TestChunkUris:
    def test_happy(self) -> None:
        uris = [f"s3://b/k-{i}" for i in range(10)]
        shards = chunk_uris(uris, max_per_shard=3)
        assert [len(s) for s in shards] == [3, 3, 3, 1]

    def test_exact_multiple(self) -> None:
        uris = [f"s3://b/k-{i}" for i in range(6)]
        shards = chunk_uris(uris, max_per_shard=2)
        assert [len(s) for s in shards] == [2, 2, 2]

    def test_empty(self) -> None:
        assert chunk_uris([], max_per_shard=10) == []

    def test_invalid_size(self) -> None:
        with pytest.raises(ValueError):
            chunk_uris(["x"], max_per_shard=0)


class TestManifestIO:
    def test_write_read_round_trip(self, s3) -> None:
        uris = ["s3://b/a.json", "s3://b/c.jsonl", "s3://b/d.jsonl"]
        written = write_manifest(s3, "test-bucket", "manifests/m.jsonl", uris)
        assert written == "s3://test-bucket/manifests/m.jsonl"
        back = read_manifest(s3, "test-bucket", "manifests/m.jsonl")
        assert back == uris

    def test_write_skips_blank_lines(self, s3) -> None:
        write_manifest(s3, "test-bucket", "m.jsonl", ["s3://b/a", "", "  ", "s3://b/b"])
        back = read_manifest(s3, "test-bucket", "m.jsonl")
        assert back == ["s3://b/a", "s3://b/b"]


class TestNormalizeInputSources:
    def test_s3_passthrough(self, s3) -> None:
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        uris = normalize_input_sources(
            s3, ["s3://some-other/foo.jsonl"], layout=L,
        )
        assert uris == ["s3://some-other/foo.jsonl"]

    def test_s3_prefix_rejected(self, s3) -> None:
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        with pytest.raises(ValueError, match="prefix inputs"):
            normalize_input_sources(s3, ["s3://x/prefix/"], layout=L)

    def test_local_file_upload(self, s3, tmp_path: Path) -> None:
        f = tmp_path / "data.jsonl"
        f.write_text('{"id":1}\n{"id":2}\n')
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        uris = normalize_input_sources(s3, [f], layout=L)
        assert uris == ["s3://test-bucket/staging/sub/inputs/data.jsonl"]
        # Verify object exists
        head = s3.head_object(Bucket="test-bucket", Key="staging/sub/inputs/data.jsonl")
        assert head["ContentType"] == "application/x-ndjson"

    def test_local_dir_upload(self, s3, tmp_path: Path) -> None:
        (tmp_path / "a.jsonl").write_text('{"id":1}\n')
        (tmp_path / "b.json").write_text('{"id":2}')
        (tmp_path / "ignored.txt").write_text("nope")
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        uris = normalize_input_sources(s3, [tmp_path], layout=L)
        names = [u.rsplit("/", 1)[-1] for u in uris]
        assert set(names) == {"a.jsonl", "b.json"}

    def test_local_dir_empty_rejected(self, s3, tmp_path: Path) -> None:
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        with pytest.raises(ValueError, match="no .json"):
            normalize_input_sources(s3, [tmp_path], layout=L)

    def test_bad_extension_rejected(self, s3, tmp_path: Path) -> None:
        f = tmp_path / "bad.txt"
        f.write_text("x")
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        with pytest.raises(ValueError, match="unsupported extension"):
            normalize_input_sources(s3, [f], layout=L)

    def test_missing_source(self, s3, tmp_path: Path) -> None:
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        with pytest.raises(FileNotFoundError):
            normalize_input_sources(s3, [tmp_path / "nope.json"], layout=L)


class TestIdempotency:
    def test_predict_output_key(self) -> None:
        L = S3Layout(bucket="out-b", submission_id="sub")
        bucket, key = predict_output_key(L, 3, "s3://in-b/prefix/foo.jsonl")
        assert bucket == "out-b"
        assert key == "outputs/sub/shard-0003/foo.jsonl"

    def test_filter_done_none_done(self, s3) -> None:
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        shards = [["s3://test-bucket/staging/sub/inputs/a.jsonl",
                   "s3://test-bucket/staging/sub/inputs/b.jsonl"]]
        filtered, skipped = filter_done(s3, L, shards)
        assert filtered == shards
        assert skipped == {}

    def test_filter_done_some_done(self, s3) -> None:
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        # Pre-populate the expected output for 'a.jsonl'
        s3.put_object(Bucket="test-bucket",
                      Key="outputs/sub/shard-0000/a.jsonl", Body=b"{}")
        shards = [[
            "s3://x/a.jsonl",
            "s3://x/b.jsonl",
            "s3://x/c.jsonl",
        ]]
        filtered, skipped = filter_done(s3, L, shards)
        assert filtered == [["s3://x/b.jsonl", "s3://x/c.jsonl"]]
        assert skipped == {0: 1}

    def test_filter_done_all_done(self, s3) -> None:
        L = S3Layout(bucket="test-bucket", submission_id="sub")
        for name in ("a.jsonl", "b.jsonl"):
            s3.put_object(Bucket="test-bucket",
                          Key=f"outputs/sub/shard-0000/{name}", Body=b"{}")
        filtered, skipped = filter_done(s3, L,
            [["s3://x/a.jsonl", "s3://x/b.jsonl"]])
        assert filtered == [[]]
        assert skipped == {0: 2}
