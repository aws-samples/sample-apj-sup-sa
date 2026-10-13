#!/usr/bin/env python3
"""워크샵용 OpenSearch Serverless 인덱싱 스크립트

S3의 bird-description 마크다운을 파싱하여 OpenSearch Serverless에
벡터 인덱싱합니다. IAM(SigV4) 인증을 사용합니다.

Usage:
    # S3에서 직접 인덱싱
    uv run python scripts/index_opensearch.py \
        --source s3://text2sql-workshop-data-{account_id}/bird-description/ \
        --opensearch-endpoint https://xxx.us-east-1.aoss.amazonaws.com \
        --region us-east-1

    # 로컬 마크다운 디렉토리에서 인덱싱
    uv run python scripts/index_opensearch.py \
        --source ./bird-benchmark/data/mini_dev/markdown_descriptions \
        --opensearch-endpoint https://xxx.us-east-1.aoss.amazonaws.com

    # 인덱스 재생성
    uv run python scripts/index_opensearch.py \
        --source s3://my-bucket/bird-description/ \
        --opensearch-endpoint https://xxx.aoss.amazonaws.com \
        --recreate
"""

import argparse
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth


INDEX_NAME = "bird-description"
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSION = 1024


@dataclass
class Chunk:
    table: str
    database: str
    chunk_type: str
    column_name: str | None
    content: str
    source_file: str


# ─── 마크다운 파싱 ────────────────────────────────────────────────────


def parse_markdown(file_path: Path) -> list[Chunk]:
    """마크다운 파일을 스키마 문서 + 샘플 쿼리 2개 청크로 파싱"""
    text = file_path.read_text(encoding="utf-8")

    table_match = re.search(r"^# Table: (.+)$", text, re.MULTILINE)
    db_match = re.search(r"\*\*Database\*\*: (.+)$", text, re.MULTILINE)
    table = table_match.group(1) if table_match else "unknown"
    database = db_match.group(1) if db_match else "unknown"

    # Sample Query 섹션 분리
    sample_match = re.search(r"\s*## Sample Query\s*\n(.+?)(?=\n## |\Z)", text, re.DOTALL)

    if sample_match:
        # 스키마 문서: Sample Query 섹션 제거
        schema_text = text[:sample_match.start()].rstrip()
        # 샘플 쿼리: 테이블/DB 컨텍스트 포함
        sample_text = f"# Table: {table}\nDatabase: {database}\n\n## Sample Query\n{sample_match.group(1).strip()}"
        return [
            Chunk(table, database, "schema", None, schema_text, str(file_path)),
            Chunk(table, database, "sample_query", None, sample_text, str(file_path)),
        ]

    return [Chunk(table, database, "schema", None, text, str(file_path))]


# ─── 임베딩 ───────────────────────────────────────────────────────────


def get_embedding(bedrock, text: str) -> list[float]:
    """Bedrock Titan으로 임베딩 생성"""
    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL,
        body=json.dumps({"inputText": text}),
    )
    return json.loads(response["body"].read())["embedding"]


# ─── OpenSearch ───────────────────────────────────────────────────────


def create_aoss_client(endpoint: str, region: str, session: boto3.Session) -> OpenSearch:
    """OpenSearch Serverless 클라이언트 생성 (SigV4 인증)"""
    credentials = session.get_credentials().get_frozen_credentials()
    awsauth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        "aoss",
        session_token=credentials.token,
    )
    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )


def create_index(client: OpenSearch, index_name: str) -> None:
    """벡터 인덱스 생성"""
    if client.indices.exists(index=index_name):
        print(f"✅ 인덱스 {index_name} 이미 존재")
        return

    client.indices.create(
        index=index_name,
        body={
            "settings": {
                "index": {"knn": True, "knn.algo_param.ef_search": 512}
            },
            "mappings": {
                "properties": {
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": EMBEDDING_DIMENSION,
                        "method": {"name": "hnsw", "engine": "faiss"},
                    },
                    "content": {"type": "text"},
                    "table": {"type": "keyword"},
                    "database": {"type": "keyword"},
                    "chunk_type": {"type": "keyword"},
                    "column_name": {"type": "keyword"},
                    "source_file": {"type": "keyword"},
                }
            },
        },
    )
    print(f"✅ 인덱스 {index_name} 생성 완료")


def index_chunks(
    client: OpenSearch, bedrock, chunks: list[Chunk], index_name: str
) -> None:
    """청크를 임베딩하여 인덱싱"""
    for i, chunk in enumerate(chunks):
        embedding = get_embedding(bedrock, chunk.content)
        doc = asdict(chunk)
        doc["embedding"] = embedding
        client.index(index=index_name, body=doc)
        if (i + 1) % 10 == 0:
            print(f"   진행: {i + 1}/{len(chunks)}")
    print(f"✅ 총 {len(chunks)}개 청크 인덱싱 완료")


# ─── 데이터 로딩 ──────────────────────────────────────────────────────


def load_from_s3(s3, source: str) -> Path:
    """S3에서 마크다운 파일 다운로드"""
    from urllib.parse import urlparse

    parsed = urlparse(source)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")

    local_dir = Path(tempfile.mkdtemp())
    paginator = s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".md"):
                rel = obj["Key"].replace(prefix, "").lstrip("/")
                local_path = local_dir / rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket, obj["Key"], str(local_path))
                count += 1
    print(f"📥 S3에서 {count}개 마크다운 다운로드 완료")
    return local_dir


# ─── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenSearch Serverless 벡터 인덱싱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", required=True, help="마크다운 소스 (s3:// 또는 로컬 경로)")
    parser.add_argument("--opensearch-endpoint", required=True, help="AOSS 엔드포인트 URL")
    parser.add_argument("--index-name", default=INDEX_NAME, help="인덱스 이름")
    parser.add_argument("--region", default="us-east-1", help="AWS 리전")
    parser.add_argument("--profile", default=None, help="AWS 프로파일")
    parser.add_argument("--recreate", action="store_true", help="인덱스 삭제 후 재생성")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    bedrock = session.client("bedrock-runtime", region_name=args.region)

    print("🚀 OpenSearch 인덱싱 시작\n")

    # 1. 데이터 로딩
    if args.source.startswith("s3://"):
        s3 = session.client("s3")
        input_path = load_from_s3(s3, args.source)
    else:
        input_path = Path(args.source)
        if not input_path.exists():
            print(f"❌ 경로를 찾을 수 없습니다: {args.source}")
            sys.exit(1)

    # 2. 마크다운 파싱
    chunks = []
    for md_file in sorted(input_path.rglob("*.md")):
        chunks.extend(parse_markdown(md_file))
    print(f"📋 총 {len(chunks)}개 청크 생성")

    if not chunks:
        print("⚠️  인덱싱할 청크가 없습니다")
        return

    # 3. OpenSearch 연결
    print(f"🔗 OpenSearch 연결: {args.opensearch_endpoint}")
    client = create_aoss_client(args.opensearch_endpoint, args.region, session)

    # 4. 인덱스 관리
    if args.recreate and client.indices.exists(index=args.index_name):
        client.indices.delete(index=args.index_name)
        print(f"🗑️ 인덱스 {args.index_name} 삭제됨")

    create_index(client, args.index_name)

    # 5. 인덱싱
    print(f"\n📤 인덱싱 중... ({args.index_name})")
    index_chunks(client, bedrock, chunks, args.index_name)

    print(f"\n{'=' * 50}")
    print("🎉 인덱싱 완료!")
    print(f"   인덱스: {args.index_name}")
    print(f"   청크 수: {len(chunks)}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
