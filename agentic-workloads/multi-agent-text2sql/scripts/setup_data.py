#!/usr/bin/env python3
"""데이터셋 셋업 스크립트

S3에 있는 zip 파일을 다운로드하여 대상 버킷에 데이터를 배포하고,
Glue Crawler를 생성하여 Athena에서 쿼리할 수 있도록 설정합니다.

Usage:
    # 기본 사용 (데이터 배포 + Glue Crawler 생성)
    uv run python bird-benchmark/scripts/setup_data.py \
        --source s3://your-data-bucket/datasets/bird-dataset.zip \
        --target-bucket my-target-bucket \
        --profile my-profile

    # 특정 DB만 배포
    uv run python bird-benchmark/scripts/setup_data.py \
        --source s3://your-data-bucket/datasets/bird-dataset.zip \
        --target-bucket my-target-bucket \
        --db-filter financial,formula_1

    # Crawler 생성 건너뛰기
    uv run python bird-benchmark/scripts/setup_data.py \
        --source s3://your-data-bucket/datasets/bird-dataset.zip \
        --target-bucket my-target-bucket \
        --skip-crawler

    # dry-run (업로드 없이 확인)
    uv run python bird-benchmark/scripts/setup_data.py \
        --source s3://your-data-bucket/datasets/bird-dataset.zip \
        --dry-run
"""

import argparse
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

# Glue Crawler 기본 설정 (bird-crawler 참고)
CRAWLER_NAME = "bird-crawler"
GLUE_DB_NAME = "ods"
GLUE_ROLE_NAME = "AWSGlueServiceRole-BirdCrawler"
BENCHMARK_PREFIX = "bird-benchmark"


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/key → (bucket, key)"""
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def ensure_bucket(s3, bucket: str, region: str, dry_run: bool) -> bool:
    """S3 버킷이 없으면 생성. 생성 여부 반환."""
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"✅ 버킷 존재: {bucket}")
        return False
    except ClientError as e:
        code = str(e.response["Error"]["Code"])
        if code in ("404", "403", "NoSuchBucket"):
            if dry_run:
                print(f"[DRY] 버킷 생성 예정: {bucket}")
                return True
            print(f"📦 버킷 생성 중: {bucket}")
            params = {"Bucket": bucket}
            if region != "us-east-1":
                params["CreateBucketConfiguration"] = {
                    "LocationConstraint": region
                }
            s3.create_bucket(**params)
            print(f"✅ 버킷 생성 완료: {bucket}")
            return True
        raise


def download_zip(s3, source: str, local_path: Path) -> None:
    """S3 또는 로컬에서 zip 파일 가져오기"""
    if source.startswith("s3://"):
        bucket, key = parse_s3_uri(source)
        print(f"📥 다운로드 중: s3://{bucket}/{key}")
        s3.download_file(bucket, key, str(local_path))
        size_mb = local_path.stat().st_size / 1024 / 1024
        print(f"   완료: {size_mb:.1f}MB")
    else:
        import shutil
        src = Path(source)
        if not src.exists():
            print(f"❌ 파일을 찾을 수 없습니다: {source}")
            sys.exit(1)
        shutil.copy2(src, local_path)
        print(f"📂 로컬 파일 사용: {source}")


def upload_files(
    s3,
    extract_dir: Path,
    target_bucket: str,
    db_filter: set[str] | None,
    dry_run: bool,
) -> dict[str, int]:
    """압축 해제된 파일을 대상 S3 버킷에 업로드"""
    stats = {"parquet": 0, "markdown": 0, "skipped": 0}
    content_types = {
        ".parquet": "application/octet-stream",
        ".md": "text/markdown; charset=utf-8",
    }

    for prefix_name in ("bird-benchmark", "bird-description"):
        prefix_dir = extract_dir / prefix_name
        if not prefix_dir.exists():
            print(f"⚠️  {prefix_name} 디렉토리 없음, 건너뜀")
            continue

        print(f"\n📤 {prefix_name} 업로드 중...")
        for file_path in sorted(prefix_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(extract_dir)
            parts = rel.parts

            if db_filter and len(parts) >= 2 and parts[1] not in db_filter:
                stats["skipped"] += 1
                continue

            s3_key = str(rel)
            suffix = file_path.suffix
            ct = content_types.get(suffix, "application/octet-stream")

            if dry_run:
                print(f"  [DRY] s3://{target_bucket}/{s3_key}")
            else:
                s3.upload_file(str(file_path), target_bucket, s3_key, ExtraArgs={"ContentType": ct})

            if suffix == ".parquet":
                stats["parquet"] += 1
            elif suffix == ".md":
                stats["markdown"] += 1

    return stats


def show_manifest(extract_dir: Path, db_filter: set[str] | None) -> None:
    """manifest.json 내용 출력"""
    manifest_path = extract_dir / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    databases = manifest.get("databases", {})
    print(f"\n📋 데이터셋 구성: {len(databases)}개 DB")
    for db_name, tables in sorted(databases.items()):
        marker = "✓" if (not db_filter or db_name in db_filter) else "○"
        print(f"  {marker} {db_name}: {len(tables)} tables")


# ─── Glue Crawler 셋업 ───────────────────────────────────────────────


GLUE_ASSUME_ROLE_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "glue.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
})


def _s3_policy_doc(bucket: str, account_id: str) -> str:
    """Glue Crawler용 S3 접근 정책 문서 생성"""
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject"],
            "Resource": [f"arn:aws:s3:::{bucket}/{BENCHMARK_PREFIX}/*"],
            "Condition": {
                "StringEquals": {"aws:ResourceAccount": account_id}
            },
        }],
    })


def ensure_glue_role(
    iam, account_id: str, bucket: str, role_name: str, dry_run: bool
) -> str:
    """Glue Crawler용 IAM Role 생성 (없으면). Role ARN 반환."""
    role_arn = f"arn:aws:iam::{account_id}:role/service-role/{role_name}"

    try:
        resp = iam.get_role(RoleName=role_name)
        print(f"✅ IAM Role 존재: {role_name}")
        return resp["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass

    if dry_run:
        print(f"[DRY] IAM Role 생성 예정: {role_name}")
        return role_arn

    print(f"🔐 IAM Role 생성 중: {role_name}")

    # Role 생성
    resp = iam.create_role(
        Path="/service-role/",
        RoleName=role_name,
        AssumeRolePolicyDocument=GLUE_ASSUME_ROLE_POLICY,
        Description="Glue Crawler role for BIRD benchmark data",
    )
    role_arn = resp["Role"]["Arn"]

    # AWS 관리형 Glue 정책 연결
    iam.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole",
    )

    # S3 접근 인라인 정책 추가
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{role_name}-s3-access",
        PolicyDocument=_s3_policy_doc(bucket, account_id),
    )

    # IAM 전파 대기
    print("   IAM 전파 대기 중 (10초)...")
    time.sleep(10)
    print(f"✅ IAM Role 생성 완료: {role_arn}")
    return role_arn


def setup_glue_crawler(
    session: boto3.Session,
    target_bucket: str,
    crawler_name: str,
    glue_db: str,
    role_name: str,
    region: str,
    run_crawler: bool,
    dry_run: bool,
) -> None:
    """Glue Database + Crawler 생성 및 실행"""
    sts = session.client("sts", region_name=region)
    account_id = sts.get_caller_identity()["Account"]

    iam = session.client("iam", region_name=region)
    glue = session.client("glue", region_name=region)

    print(f"\n{'=' * 50}")
    print(f"🔧 Glue Crawler 셋업 {'(DRY RUN)' if dry_run else ''}")
    print(f"   Crawler: {crawler_name}")
    print(f"   Database: {glue_db}")
    print(f"   Target: s3://{target_bucket}/{BENCHMARK_PREFIX}/")
    print(f"{'=' * 50}\n")

    # 1. IAM Role
    role_arn = ensure_glue_role(iam, account_id, target_bucket, role_name, dry_run)

    # 2. Glue Database
    try:
        glue.get_database(Name=glue_db)
        print(f"✅ Glue Database 존재: {glue_db}")
    except glue.exceptions.EntityNotFoundException:
        if dry_run:
            print(f"[DRY] Glue Database 생성 예정: {glue_db}")
        else:
            print(f"📊 Glue Database 생성 중: {glue_db}")
            glue.create_database(
                DatabaseInput={"Name": glue_db, "Description": "BIRD benchmark data"}
            )
            print(f"✅ Glue Database 생성 완료: {glue_db}")

    # 3. Crawler
    s3_target = f"s3://{target_bucket}/{BENCHMARK_PREFIX}/"
    crawler_config = {
        "Name": crawler_name,
        "Role": role_arn,
        "DatabaseName": glue_db,
        "Targets": {"S3Targets": [{"Path": s3_target, "Exclusions": []}]},
        "RecrawlPolicy": {"RecrawlBehavior": "CRAWL_EVERYTHING"},
        "SchemaChangePolicy": {
            "UpdateBehavior": "UPDATE_IN_DATABASE",
            "DeleteBehavior": "DEPRECATE_IN_DATABASE",
        },
        "Configuration": json.dumps({"Version": 1.0, "CreatePartitionIndex": True}),
    }

    try:
        glue.get_crawler(Name=crawler_name)
        print(f"✅ Crawler 존재: {crawler_name}")
        if not dry_run:
            # 타겟 버킷이 다를 수 있으니 업데이트
            glue.update_crawler(**crawler_config)
            print(f"   Crawler 설정 업데이트 완료")
    except glue.exceptions.EntityNotFoundException:
        if dry_run:
            print(f"[DRY] Crawler 생성 예정: {crawler_name}")
        else:
            print(f"🕷️ Crawler 생성 중: {crawler_name}")
            glue.create_crawler(**crawler_config)
            print(f"✅ Crawler 생성 완료: {crawler_name}")

    # 4. Crawler 실행
    if run_crawler and not dry_run:
        print(f"\n▶️  Crawler 실행 중: {crawler_name}")
        try:
            glue.start_crawler(Name=crawler_name)
            print("   Crawler가 시작되었습니다.")
            print("   완료까지 1~3분 소요됩니다.")
            print(f"   상태 확인: aws glue get-crawler --name {crawler_name} --query 'Crawler.State'")
        except glue.exceptions.CrawlerRunningException:
            print("   Crawler가 이미 실행 중입니다.")


# ─── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="데이터셋 셋업 - S3 zip 배포 + Glue Crawler 생성",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # 전체 셋업 (S3 배포 + Glue Crawler)
    uv run python bird-benchmark/scripts/setup_data.py \\
        --source s3://source-bucket/datasets/bird-dataset.zip \\
        --target-bucket my-bucket --profile my-profile

    # 특정 DB만 배포
    uv run python bird-benchmark/scripts/setup_data.py \\
        --source s3://source-bucket/datasets/bird-dataset.zip \\
        --target-bucket my-bucket --db-filter financial,formula_1

    # Crawler 없이 데이터만 배포
    uv run python bird-benchmark/scripts/setup_data.py \\
        --source s3://source-bucket/datasets/bird-dataset.zip \\
        --target-bucket my-bucket --skip-crawler

    # dry-run
    uv run python bird-benchmark/scripts/setup_data.py \\
        --source s3://source-bucket/datasets/bird-dataset.zip \\
        --dry-run
        """,
    )
    parser.add_argument("--source", required=True, help="zip 파일 위치 (s3:// 또는 로컬)")
    parser.add_argument("--target-bucket", help="데이터 배포할 S3 버킷")
    parser.add_argument("--profile", default=None, help="AWS 프로파일")
    parser.add_argument("--region", default="us-east-1", help="AWS 리전")
    parser.add_argument("--db-filter", default=None, help="배포할 DB (쉼표 구분)")
    parser.add_argument("--crawler-name", default=CRAWLER_NAME, help="Glue Crawler 이름")
    parser.add_argument("--glue-db", default=GLUE_DB_NAME, help="Glue Database 이름")
    parser.add_argument("--glue-role", default=GLUE_ROLE_NAME, help="Glue IAM Role 이름")
    parser.add_argument("--skip-crawler", action="store_true", help="Crawler 생성 건너뛰기")
    parser.add_argument("--no-run-crawler", action="store_true", help="Crawler 생성만 하고 실행 안 함")
    parser.add_argument("--dry-run", action="store_true", help="업로드/생성 없이 확인만")
    args = parser.parse_args()

    if not args.dry_run and not args.target_bucket:
        parser.error("--target-bucket 필수 (또는 --dry-run 사용)")

    db_filter = set(args.db_filter.split(",")) if args.db_filter else None
    target = args.target_bucket or "dry-run-bucket"

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = session.client("s3")

    print("🚀 데이터셋 셋업 시작\n")

    # 1. 버킷 확인/생성
    if not args.dry_run:
        ensure_bucket(s3, target, args.region, dry_run=False)
    else:
        ensure_bucket(s3, target, args.region, dry_run=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        zip_path = tmp / "dataset.zip"

        # 2. zip 다운로드
        download_zip(s3, args.source, zip_path)

        # 3. 압축 해제
        print("📂 압축 해제 중...")
        extract_dir = tmp / "extracted"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        print("   완료")

        # 4. manifest 확인
        show_manifest(extract_dir, db_filter)

        # 5. 파일 업로드
        print(f"\n{'=' * 50}")
        print(f"📤 데이터 배포: s3://{target}/")
        if db_filter:
            print(f"   필터: {', '.join(sorted(db_filter))}")
        if args.dry_run:
            print("   모드: DRY RUN")
        print(f"{'=' * 50}")

        stats = upload_files(s3, extract_dir, target, db_filter, args.dry_run)

        print(f"\n✅ 데이터 배포 완료!")
        print(f"   Parquet: {stats['parquet']}개 | Markdown: {stats['markdown']}개")
        if stats["skipped"]:
            print(f"   건너뜀: {stats['skipped']}개")

    # 6. Glue Crawler
    if not args.skip_crawler:
        setup_glue_crawler(
            session=session,
            target_bucket=target,
            crawler_name=args.crawler_name,
            glue_db=args.glue_db,
            role_name=args.glue_role,
            region=args.region,
            run_crawler=not args.no_run_crawler,
            dry_run=args.dry_run,
        )

    print(f"\n{'=' * 50}")
    print("🎉 셋업 완료!")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
