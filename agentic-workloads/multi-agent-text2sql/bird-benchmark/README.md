# BIRD Benchmark Evaluation

Multi-Agent Text2SQL 시스템을 BIRD 벤치마크로 평가하기 위한 디렉토리입니다.

## 데이터셋 다운로드

### Mini-Dev (권장 - 로컬 테스트용)

```bash
git clone https://github.com/bird-bench/mini_dev.git data/mini_dev
```

### Full Dev Set

공식 사이트에서 다운로드: https://bird-bench.github.io/

## 디렉토리 구조

```
bird-benchmark/
├── README.md
├── requirements.txt
├── data/
│   └── mini_dev/
│       └── markdown_descriptions/   # 마크다운 변환된 테이블 설명
└── scripts/
    ├── csv_to_markdown.py           # CSV → Markdown 변환
    ├── sqlite_upload_to_s3.py       # SQLite → Parquet → S3 업로드
    ├── generate_gold_results.py     # Gold SQL 실행 결과 생성
    └── indexer.py                   # OpenSearch 벡터 인덱싱
```

## Scripts

### 1. csv_to_markdown.py
BIRD benchmark의 database_description CSV를 RAG용 Markdown으로 변환

```bash
python scripts/csv_to_markdown.py
```

### 2. sqlite_upload_to_s3.py
SQLite → Parquet 변환 후 S3 업로드

```bash
python scripts/sqlite_upload_to_s3.py --bucket your-bucket-name
```

### 3. generate_gold_results.py
Gold SQL 실행 결과를 JSON으로 저장

```bash
python scripts/generate_gold_results.py
```

### 4. indexer.py
마크다운을 청킹 → Titan Embedding v2 벡터화 → OpenSearch 인덱싱

```bash
python scripts/indexer.py s3://text2sql-data-your-account-id/bird-description --profile demo
```

**OpenSearch 검색 예시:**
```json
GET bird-description/_search
{
  "size": 5,
  "query": { "match": { "content": "transaction amount" } }
}
```

## S3 데이터 구조

```
s3://text2sql-data-your-account-id/
├── bird-benchmark/          # Parquet 데이터
└── bird-description/        # 마크다운 테이블 설명
```
