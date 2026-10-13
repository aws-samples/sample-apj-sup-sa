{eval_file} 파일을 읽고 각 case의 generated_sql을 gold_sql과 비교하여 채점해줘.

중요: 모든 case를 반드시 하나씩 채점해야 해. 스킵하거나 생략하지 마.

채점 기준:
- CORRECT: generated_sql이 gold_sql과 동일한 결과를 반환할 것으로 판단
- ACCEPTABLE: 정답을 직접 반환하진 않지만, 결과에서 정답을 쉽게 도출 가능
  (한 단계 추가 집계/필터링이면 충분한 경우. 예: 차이를 구해야 하는데 각 값을 별도 행으로 반환)
- WRONG: SQL 로직이 달라서 다른 결과가 나올 것으로 판단
- ERROR: generated_sql이 null이거나 실행 불가능한 경우

판단 시 고려사항:
- text2sql 서비스 관점에서 채점. 사용자가 결과를 보고 납득할 수 있으면 ACCEPTABLE
- gold_result는 보지 말고, gold_sql과 generated_sql을 직접 비교하여 판단할 것
- 질문(question)도 반드시 확인하고, gold_sql보다 generated_sql이 질문 의도에 더 부합하면 ACCEPTABLE 이상으로 판정
  (예: 질문이 "how many customers"인데 gold가 COUNT(*), generated가 COUNT(DISTINCT customerid)면 generated가 더 합당)
- SQL 방언 차이는 무시 (IIF↔CASE WHEN, SUBSTR↔SUBSTRING, CAST 타입 차이, ods. 스키마 접두사, LIMIT 추가 등)
- 결과 형태 차이도 허용 (별칭 다름, 컬럼 순서 다름, ROUND 차이 등)
- 핵심은 "같은 질문에 대해 같은 숫자/값이 나오는가"
- 애매한 경우 evidence(힌트)도 참고

채점 결과를 eval_results/ 디렉토리에 저장해줘.
파일명: 원본 파일명 앞에 graded_ 붙여서

저장 형식 (JSON):
{
  "source_file": "원본 파일 경로",
  "model_id": "원본에서 가져옴",
  "total": 전체 수,
  "correct": N,
  "acceptable": N,
  "wrong": N,
  "errors": N,
  "accuracy_strict": correct / total,
  "accuracy_lenient": (correct + acceptable) / total,
  "grading_criteria": { 각 등급 설명 },
  "cases": [
    {
      "index": 원본 index,
      "db_id": "...",
      "difficulty": "...",
      "question": "...",
      "grade": "CORRECT|ACCEPTABLE|WRONG|ERROR",
      "reason": "판정 사유 한 줄",
      "gold_sql": "...",
      "generated_sql": "..."
    }
  ],
  "summary": {
    "by_difficulty": {"simple": {"total": N, "correct": N, "acceptable": N, "wrong": N}, ...},
    "by_db": {"db_id": {"total": N, "correct": N, "acceptable": N, "wrong": N}, ...},
    "wrong_patterns": ["틀린 문제들의 공통 패턴 분석"]
  }
}
