"""BIRD Benchmark Evaluation Script

BIRD mini_dev 질문을 멀티에이전트 시스템에 보내고
생성된 SQL을 gold SQL/결과와 함께 저장합니다.
채점은 별도 LLM으로 수행합니다.

Usage:
    # Step 1: 샘플 파일 생성 (한 번만)
    uv run python scripts/evaluate.py --generate-sample 100
    uv run python scripts/evaluate.py --generate-sample 50 --db thrombosis_prediction
    uv run python scripts/evaluate.py --generate-sample 80 --difficulty simple

    # Step 2: 샘플 파일로 평가 실행
    uv run python scripts/evaluate.py --sample-file eval_samples/sample_100_seed42.json
    uv run python scripts/evaluate.py --sample-file eval_samples/sample_100_seed42.json --limit 10

    # 이전 결과 이어서 실행 (중단된 경우)
    uv run python scripts/evaluate.py --sample-file eval_samples/sample_100_seed42.json --resume eval_results/20260311_143022.json

    # 특정 인덱스만 실행 (샘플 파일 없이 ad-hoc)
    uv run python scripts/evaluate.py --indices 0,1,5,10
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BIRD_DATA_DIR = PROJECT_ROOT / "bird-benchmark" / "data" / "mini_dev" / "llm" / "mini_dev_data"
BIRD_QUESTIONS = BIRD_DATA_DIR / "mini_dev_sqlite.json"
BIRD_GOLD_RESULTS = BIRD_DATA_DIR / "mini_dev_sqlite_gold_results.json"
BIRD_GOLD_SQL = BIRD_DATA_DIR / "mini_dev_sqlite_gold.sql"
EVAL_RESULTS_DIR = PROJECT_ROOT / "eval_results"
EVAL_SAMPLES_DIR = PROJECT_ROOT / "eval_samples"


def load_env():
    """환경변수 파일 로드"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


def load_bird_data() -> List[Dict[str, Any]]:
    """BIRD mini_dev 데이터 로드 (질문 + gold SQL + gold 결과 통합)"""
    with open(BIRD_QUESTIONS) as f:
        questions = json.load(f)

    with open(BIRD_GOLD_RESULTS) as f:
        gold_results = json.load(f)

    gold_sqls = []
    with open(BIRD_GOLD_SQL) as f:
        for line in f:
            sql = line.strip().split('\t')[0] if '\t' in line else line.strip()
            gold_sqls.append(sql)

    data = []
    for i, q in enumerate(questions):
        entry = {
            "index": i,
            "question_id": q.get("question_id"),
            "db_id": q["db_id"],
            "question": q["question"],
            "evidence": q.get("evidence", ""),
            "difficulty": q.get("difficulty", "unknown"),
            "gold_sql": gold_sqls[i] if i < len(gold_sqls) else "",
            "gold_result": gold_results[i].get("result") if i < len(gold_results) else None,
        }
        data.append(entry)

    return data


def generate_sample_file(
    data: List[Dict],
    sample_size: int,
    db_filter: Optional[str] = None,
    difficulty_filter: Optional[str] = None,
    seed: int = 42,
) -> Path:
    """샘플 파일 생성 — 인덱스 목록을 고정하여 재사용"""
    filtered = data
    if db_filter:
        filtered = [d for d in filtered if d["db_id"] == db_filter]
    if difficulty_filter:
        filtered = [d for d in filtered if d["difficulty"] == difficulty_filter]

    if sample_size < len(filtered):
        random.seed(seed)
        filtered = random.sample(filtered, sample_size)

    filtered = sorted(filtered, key=lambda d: d["index"])
    indices = [d["index"] for d in filtered]

    # 파일명: sample_100_seed42.json 또는 sample_50_thrombosis_prediction_seed42.json
    parts = [f"sample_{len(indices)}"]
    if db_filter:
        parts.append(db_filter)
    if difficulty_filter:
        parts.append(difficulty_filter)
    parts.append(f"seed{seed}")
    filename = "_".join(parts) + ".json"

    EVAL_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = EVAL_SAMPLES_DIR / filename

    sample_data = {
        "created": datetime.now().isoformat(),
        "seed": seed,
        "total_pool": len(data),
        "filters": {"db": db_filter, "difficulty": difficulty_filter},
        "count": len(indices),
        "indices": indices,
        "questions": [
            {
                "index": d["index"],
                "question_id": d["question_id"],
                "db_id": d["db_id"],
                "difficulty": d["difficulty"],
                "question": d["question"],
            }
            for d in filtered
        ],
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(sample_data, f, ensure_ascii=False, indent=2)

    return output_path


def load_sample_indices(sample_file: str) -> List[int]:
    """샘플 파일에서 인덱스 목록 로드"""
    with open(sample_file) as f:
        sample_data = json.load(f)
    return sample_data["indices"]


def select_questions(
    data: List[Dict],
    indices: List[int],
    limit: Optional[int] = None,
) -> List[Dict]:
    """인덱스 목록으로 질문 선택 (limit으로 앞에서 N개만)"""
    index_set = set(indices)
    selected = [d for d in data if d["index"] in index_set]
    selected = sorted(selected, key=lambda d: d["index"])
    if limit and limit < len(selected):
        selected = selected[:limit]
    return selected


def extract_sql(text: str) -> Optional[str]:
    """응답 텍스트에서 SQL 추출"""
    if not text:
        return None

    # ```sql ... ``` 블록 (마지막 것 = response_node의 최종 SQL)
    sql_blocks = re.findall(r'```sql\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if sql_blocks:
        return sql_blocks[-1].strip()

    # SELECT ... 문장
    select_match = re.search(
        r'(SELECT\s+.+?FROM\s+.+?)(?:\n\n|$)',
        text, re.DOTALL | re.IGNORECASE
    )
    if select_match:
        return select_match.group(1).strip()

    return None


def run_question(agent, question: str) -> Dict[str, Any]:
    """단일 질문 실행, SQL과 응답 텍스트 수집"""
    full_response = ""
    sql_node_text = ""
    error = None

    start = time.time()
    try:
        for event in agent.stream_response(question):
            event_type = event.get("type", "")

            if "data" in event:
                text = event.get("data", "")
                full_response += text
                if event.get("agent") == "sql_node":
                    sql_node_text += text

            if event_type == "force_stop":
                error = event.get("force_stop_reason", "Unknown error")
                break

            if event_type == "complete":
                break

    except Exception as e:
        error = str(e)

    elapsed = time.time() - start

    # SQL 추출: sql_node 텍스트 우선, 없으면 전체 응답에서 추출
    generated_sql = extract_sql(sql_node_text) or extract_sql(full_response)

    return {
        "generated_sql": generated_sql,
        "elapsed_s": round(elapsed, 1),
        "error": error,
    }


def save_results(results: Dict[str, Any], output_path: Path):
    """결과 JSON 저장"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="BIRD Benchmark Evaluation")

    # Step 1: 샘플 생성
    parser.add_argument("--generate-sample", type=int, default=None, metavar="N",
                        help="샘플 파일 생성 (N개) 후 종료")
    parser.add_argument("--db", type=str, default=None, help="DB 필터 (생성/ad-hoc 시)")
    parser.add_argument("--difficulty", type=str, default=None, help="난이도 필터")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드")

    # Step 2: 평가 실행
    parser.add_argument("--sample-file", type=str, default=None, help="샘플 파일 경로")
    parser.add_argument("--limit", type=int, default=None, help="샘플에서 앞 N개만 실행")
    parser.add_argument("--indices", type=str, default=None, help="특정 인덱스만 (콤마 구분)")
    parser.add_argument("--model", type=str, default="global.anthropic.claude-haiku-4-5-20251001-v1:0")

    # parser.add_argument("--model", type=str, default="global.anthropic.claude-opus-4-6-v1")
    parser.add_argument("--resume", type=str, default=None, help="이전 결과 이어서 실행")
    parser.add_argument("--output", type=str, default=None, help="출력 파일 경로")

    args = parser.parse_args()
    load_env()

    # 데이터 로드
    print("Loading BIRD data...")
    all_data = load_bird_data()
    print(f"  Total: {len(all_data)} questions")

    # ── Step 1: 샘플 생성 모드 ──
    if args.generate_sample:
        path = generate_sample_file(
            all_data,
            sample_size=args.generate_sample,
            db_filter=args.db,
            difficulty_filter=args.difficulty,
            seed=args.seed,
        )
        print(f"\nSample file created: {path}")
        with open(path) as f:
            info = json.load(f)
        print(f"  Count: {info['count']}")
        print(f"  Seed: {info['seed']}")
        print(f"  Filters: {info['filters']}")

        # 난이도/DB 분포 표시
        from collections import Counter
        qs = info["questions"]
        diff_dist = Counter(q["difficulty"] for q in qs)
        db_dist = Counter(q["db_id"] for q in qs)
        print(f"  Difficulty: {dict(diff_dist)}")
        print(f"  DB: {dict(db_dist)}")
        return

    # ── Step 2: 평가 실행 모드 ──
    # 질문 선택
    if args.sample_file:
        sample_indices = load_sample_indices(args.sample_file)
        questions = select_questions(all_data, sample_indices, limit=args.limit)
        print(f"  Sample file: {args.sample_file} ({len(sample_indices)} questions)")
    elif args.indices:
        idx_list = [int(x) for x in args.indices.split(",")]
        questions = select_questions(all_data, idx_list)
    else:
        print("Error: --sample-file 또는 --indices 필요 (먼저 --generate-sample로 샘플 생성)")
        sys.exit(1)

    # 이전 결과에서 이어서 실행
    completed_indices = set()
    previous_cases = []
    if args.resume:
        with open(args.resume) as f:
            prev = json.load(f)
        previous_cases = prev.get("cases", [])
        completed_indices = {c["index"] for c in previous_cases if c.get("generated_sql") or c.get("error")}
        print(f"  Resuming: {len(completed_indices)} already completed")

    pending = [q for q in questions if q["index"] not in completed_indices]
    print(f"  Selected: {len(questions)} questions ({len(pending)} pending)\n")

    if not pending:
        print("Nothing to run.")
        return

    # 출력 파일 경로
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = EVAL_RESULTS_DIR / f"{timestamp}.json"

    # 에이전트 초기화
    from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL

    print(f"Initializing agent (model: {args.model})...")
    agent = MultiAgentText2SQL(args.model, force_data_query=True)
    print("Agent ready.\n")

    cases = {c["index"]: c for c in previous_cases}

    print(f"{'='*70}")
    print(f"  BIRD Evaluation - {len(pending)} questions")
    print(f"  Model: {args.model}")
    print(f"  Output: {output_path}")
    print(f"{'='*70}\n")

    for i, q in enumerate(pending, 1):
        idx = q["index"]
        print(f"[{i}/{len(pending)}] #{idx} ({q['difficulty']}) {q['db_id']}")
        print(f"  Q: {q['question'][:80]}...")

        agent.reset_context()
        result = run_question(agent, q["question"])

        case = {
            "index": idx,
            "question_id": q["question_id"],
            "db_id": q["db_id"],
            "difficulty": q["difficulty"],
            "question": q["question"],
            "evidence": q["evidence"],
            "gold_sql": q["gold_sql"],
            "gold_result": q["gold_result"],
            "generated_sql": result["generated_sql"],
            "elapsed_s": result["elapsed_s"],
            "error": result["error"],
        }
        cases[idx] = case

        if result["error"]:
            print(f"  ERROR: {result['error'][:80]}")
        elif result["generated_sql"]:
            sql_preview = result["generated_sql"].replace('\n', ' ')[:80]
            print(f"  SQL: {sql_preview}...")
        else:
            print(f"  SQL: (extraction failed)")
        print(f"  Time: {result['elapsed_s']}s\n")

        # 매 질문마다 중간 저장
        output_data = {
            "timestamp": timestamp,
            "model_id": args.model,
            "sample_file": args.sample_file,
            "total": len(questions),
            "completed": len(cases),
            "cases": sorted(cases.values(), key=lambda c: c["index"]),
        }
        save_results(output_data, output_path)

    # 최종 요약
    all_cases = sorted(cases.values(), key=lambda c: c["index"])
    sql_extracted = sum(1 for c in all_cases if c.get("generated_sql"))
    errors = sum(1 for c in all_cases if c.get("error"))

    print(f"\n{'='*70}")
    print(f"  DONE")
    print(f"  Total: {len(all_cases)}")
    print(f"  SQL extracted: {sql_extracted}")
    print(f"  Errors: {errors}")
    print(f"  Output: {output_path}")
    print(f"{'='*70}")

    if hasattr(agent, 'close'):
        agent.close()


if __name__ == "__main__":
    main()
