"""멀티에이전트 시스템 상수 정의

모든 에이전트에서 공유되는 상수들을 중앙 집중식으로 관리합니다.
"""

import os
import re

# =============================================================================
# Athena 설정
# =============================================================================

# 기본 카탈로그 및 워크그룹
DEFAULT_CATALOG = "AwsDataCatalog"
DEFAULT_WORKGROUP = "primary"

# Athena 출력 위치 (환경 변수에서 로드)
# 주의: ATHENA_OUTPUT_LOCATION 환경변수를 반드시 설정해야 합니다
ATHENA_OUTPUT_LOCATION = os.environ.get("ATHENA_OUTPUT_LOCATION")
if not ATHENA_OUTPUT_LOCATION:
    raise EnvironmentError(
        "ATHENA_OUTPUT_LOCATION 환경변수가 설정되지 않았습니다. "
        "예: export ATHENA_OUTPUT_LOCATION=s3://your-athena-results-bucket"
    )

# =============================================================================
# 쿼리 실행 설정 (Requirements 3.4, 3.5)
# =============================================================================

# 폴링 설정
POLLING_INTERVAL_SECONDS = 5
MAX_POLLING_ATTEMPTS = 5

# 결과 제한
MAX_QUERY_RESULTS = 1000

# =============================================================================
# 데이터 탐색 설정 (Requirements 2.2)
# =============================================================================

# 테이블 메타데이터 수집 제한
MAX_TABLES_PER_DATABASE = 50

# =============================================================================
# 컬럼 타입 패턴
# =============================================================================

# 날짜/시간 관련 컬럼 타입
DATE_TIME_TYPES = {"date", "timestamp", "datetime"}

# 날짜 컬럼 이름 패턴
DATE_COLUMN_PATTERNS = re.compile(
    r"(date|time|timestamp|created|updated|modified|_at$|_dt$|_date$)",
    re.IGNORECASE
)

# 숫자형 컬럼 타입
NUMERIC_TYPES = {
    "int", "integer", "bigint", "smallint", "tinyint",
    "float", "double", "decimal", "numeric", "real"
}
