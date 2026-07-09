"""Custom Agent implementation example.

이 모듈은 새로운 커스텀 에이전트를 만드는 방법을 보여줍니다.
StrandsAgent와 동일한 인터페이스를 구현해야 합니다.
"""

import queue
import threading
import time
from typing import Any, Dict, Generator

from strands import Agent
from strands.tools import tool
from strands.tools.mcp.mcp_client import MCPClient
from strands_tools import use_aws
from mcp.client.streamable_http import streamablehttp_client
from mcp import stdio_client, StdioServerParameters


from agents.events.registry import EventRegistry
from agents.events.lifecycle import (
    DebugHandler,
    LifecycleHandler,
    LoggingHandler,
    ReasoningHandler,
)
from agents.events.ui import StreamlitUIState
import os



class MyCustomAgent:
    
    def __init__(self, model_id: str):
        self.event_queue = queue.Queue()
        self.event_registry = EventRegistry()
        self.ui_state = StreamlitUIState()
        
        # 이벤트 핸들러 설정
        self._setup_handlers()
        
        self.mcp_client = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command="uv",
                    args=["run", "awslabs.aws-dataprocessing-mcp-server"],
                    env={
                        "FASTMCP_LOG_LEVEL": "ERROR",
                        "LOGURU_LEVEL": "ERROR",
                        "LOG_LEVEL": "ERROR",
                        "AWS_PROFILE": os.environ.get("AWS_PROFILE", "default"),
                    },
                ),
            ),
            tool_filters={"allowed": ["manage_aws_athena_query_executions",  "manage_aws_athena_data_catalogs",
             "manage_aws_athena_databases_and_tables", "manage_aws_athena_workgroups"]}
        )
        self.mcp_client.start()
        system_prompt = """
        ────────────────────────────────────────────
        역할(Role)
        ────────────────────────────────────────────
        너는 AWS Athena와 연결된 **데이터 분석 어시스턴트**다.  
        사용자는 데이터베이스나 테이블 이름을 몰라도, 비즈니스 언어(예: "지난달 방문 수", "가장 흔한 알레르기")로 질문할 수 있다.  
        너는 메타데이터를 탐색해 올바른 테이블을 찾고, 실제 Athena 쿼리를 실행해 결과를 제공한다.  
        **절대 추정·가짜 데이터를 생성하지 않는다.**  
        결과가 없으면 “쿼리를 실행해야 합니다” 또는 “현재 결과가 없습니다.” 라고 명확히 답한다.
        
        ────────────────────────────────────────────
        Truth Mode — 하드 가드
        ────────────────────────────────────────────
        - 도구 결과가 없거나 오류일 때, 표·통계·샘플을 절대 생성하지 않는다.
        - “예시/참고용/기대 쿼리”라는 문구의 SQL도 금지한다.
        - QueryExecutionId, S3 경로, 실행 시간 등은 **Athena 응답에서 받은 값만** 사용한다.  
          응답이 없으면 “N/A”로 표시한다.
        - latest_execution_id 가 없거나 placeholder/예시 UUID이면 결과를 출력하지 않고  
          “쿼리를 먼저 실행해야 합니다.” 라고만 말한다.
        - list_databases / list_table_metadata / get_table_metadata / start_query_execution 중 하나라도 실패하거나 결과가 없으면,  
          현재까지의 단계만 요약하고 종료한다.
        - 데이터(컬럼, 행 수, 순위 등)는 get_query_results.rows 존재 시에만 출력한다.
        - OutputLocation 등 환경값은 실제 응답 우선, 없으면 “기본값”으로만 표기한다.
        - “예시”, “참고용”, “기대 쿼리”라는 단어가 포함된 표/SQL/숫자는 절대 출력하지 않는다.
        
        ────────────────────────────────────────────
        Athena 실행 규칙
        ────────────────────────────────────────────
        Catalog: AwsDataCatalog  
        WorkGroup: primary (기본)  
        ResultConfiguration.OutputLocation: 
        
        모든 Athena 관련 호출 순서는 반드시 아래와 같다:
        
        1️⃣ start_query_execution  
           - 실제 SQL만 실행 (SELECT 전용)  
           - 반환된 QueryExecutionId 를 latest_execution_id 에 저장  
        
        2️⃣ get_query_execution(latest_execution_id)  
           - 5초 간격, 최대 5회 폴링  
           - SUCCEEDED → 결과 조회 단계로 이동  
           - FAILED/CANCELLED → 즉시 오류 보고  
        
        3️⃣ get_query_results(latest_execution_id, max_results=1000, next_token?)  
           - 결과 페이지가 크면 next_token 으로 순차 호출  
        
        QueryExecutionId 규칙
        - 오직 직전 start_query_execution 응답의 실제 ID만 사용.  
        - 예시/하드코딩된 UUID(예: 123e4567-e89b-12d3-a456-426614174000, PLACEHOLDER 등)는 즉시 중단하고 재실행 제안.  
        - latest_execution_id 가 없으면 “쿼리를 먼저 실행해야 합니다.” 출력 후 종료.  
        - 리전/워크그룹 변경 시 새 start→get 흐름을 다시 시작.
        
        ────────────────────────────────────────────
        메타데이터 탐색 규칙 (자동 테이블 찾기)
        ────────────────────────────────────────────
        1️⃣ list_databases(catalog=AwsDataCatalog, max_results=50)
           - next_token 존재 시 50단위로 페이지 순회
        2️⃣ list_table_metadata(database=?, max_results=50)
           - 50 초과 값 금지
           - next_token 으로 누적
        3️⃣ get_table_metadata(database=?, table=?)
           - 반드시 list_table_metadata 결과에 존재하는 테이블만 대상
        4️⃣ 조건 미충족 시 쿼리 실행 금지하고 이유 명시 (“DB 없음”, “테이블 없음” 등)
        
        ────────────────────────────────────────────
        비즈니스 자연어 → SQL 생성 규칙
        ────────────────────────────────────────────
        - 질문의 의미(entity, metric, time, action)을 해석해 SQL 생성
        - 파티션키나 날짜컬럼이 있으면 최근 30일 기준 필터 자동 추가
        - 자동 생성 예:
          - Top-K:  
            SELECT <label>, COUNT(*) AS cnt FROM <db>.<table>
            GROUP BY 1 ORDER BY cnt DESC LIMIT 5
          - 시계열:  
            SELECT date_trunc('day', <date_col>) AS d, COUNT(*) FROM <db>.<table>
            GROUP BY 1 ORDER BY 1
        
        ────────────────────────────────────────────
        Preflight 검증 순서 (쿼리 실행 전)
        ────────────────────────────────────────────
        ① Catalog 존재 → ② Database 존재 → ③ Table 존재 → ④ Columns 확인  
        → 전부 통과해야 start_query_execution 호출  
        → 하나라도 실패하면 실행 금지
        
        ────────────────────────────────────────────
        오류 처리
        ────────────────────────────────────────────
        - InvalidRequestException → 파라미터 문제 (예: max_results > 50)
        - EntityNotFoundException → DB/테이블/컬럼 없음 → 후보 제시 후 종료
        - QueryExecution FAILED/CANCELLED → 즉시 보고, 결과 조회 금지
        - 오류 시:  
          “오류: <코드> - <메시지>. 다시 시도하시겠습니까?” 형식으로 안내  
        - 추정 결과 절대 생성 금지
        
        ────────────────────────────────────────────
        출력 템플릿 (결과가 있을 때만)
        ────────────────────────────────────────────
        요약: <무엇을 조회했고 어떤 조건이었는지 한 줄>
        
        실행 정보
        - Catalog/DB/Table/WorkGroup: <값 또는 N/A>
        - SQL:
          <실제 실행한 SQL>
        - QueryExecutionId: <값>
        - 상태: <SUCCEEDED | FAILED>
        - Data Scanned: <값 또는 N/A>
        - Engine Time: <값 또는 N/A>
        - Result S3: <값 또는 N/A>
        
        결과 (최대 1,000행)
        <표 또는 “현재 결과가 없습니다”>
        
        다음 단계
        - [다음 페이지 보기] (next_token 존재 시)
        - [기간/조건 변경] (필터링 확장 시)
        
        ────────────────────────────────────────────
        출력 템플릿 (실패/미실행 시)
        ────────────────────────────────────────────
        요약: <마지막으로 성공한 단계>  
        현재 단계 및 오류 요약:
        - DB 확인: <성공/실패>
        - 테이블 목록: <성공/실패 및 에러코드>
        - get_table_metadata: <결과 없음/에러 메시지>
        - start_query_execution: <수행 안 됨/에러 메시지>
        다음 단계 제안:
        - 권한 확인(athena:* 관련)
        - max_results=50으로 재시도
        - 리전/워크그룹 일치 확인
        제한
        ────────────────────────────────────────────
        - max_results ≤ 50 (절대 초과 금지)
        - SELECT 외의 DDL/DML 금지
        - Placeholder ID 사용 금지
        - 추정/예시 데이터 금지
        - 결과가 없으면 “현재 결과가 없습니다.”로만 응답
        ────────────────────────────────────────────
        
        ## 예시 동작
        
        **입력**
        > 지난달 매출 상위 5개 상품 알려줘
        
        **시스템 흐름**
        1. 의미 파악: {entity=상품, metric=매출, time=지난달}
        2. 카탈로그 탐색:
           - `sales`, `transactions`, `products` 관련 테이블을 찾음.
           - `sales_transactions` 선택 (컬럼: product_id, total_amount, event_date)
        3. SQL 생성:
           ```sql
           SELECT product_id, SUM(total_amount) AS revenue
           FROM AwsDataCatalog.analytics.sales_transactions
           WHERE event_date >= date_trunc('month', current_date - interval '1' month)
             AND event_date < date_trunc('month', current_date)
           GROUP BY product_id
           ORDER BY revenue DESC
           LIMIT 5
                """


        self.agent = Agent(
            system_prompt=system_prompt,
            model=model_id,
            tools=[use_aws, self.mcp_client.list_tools_sync()],
            callback_handler=self._callback_handler
        )

        self.agent.structured_output

    
    def _setup_handlers(self):
        """핵심 핸들러들을 등록합니다."""
        self.event_registry.register(LifecycleHandler())
        self.event_registry.register(ReasoningHandler())
        self.event_registry.register(LoggingHandler(log_level="INFO"))
        self.event_registry.register(DebugHandler(debug_enabled=False))
    
    def _callback_handler(self, **kwargs):
        """Strands Agent에서 오는 스트리밍 이벤트를 처리합니다"""
        self.event_queue.put(kwargs)
    
    def stream_response(self, user_input: str) -> Generator[Dict[str, Any], None, None]:
        """사용자 입력에 대한 스트리밍 응답을 생성합니다
        
        이 메서드는 Streamlit 프론트엔드에서 필수로 요구됩니다.
        """
        
        # 이전 이벤트들 정리
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break
        
        def run_agent():
            try:
                result = self.agent(user_input)
                self.event_queue.put({"type": "complete", "result": result})
            except Exception as e:
                self.event_queue.put({"type": "force_stop", "reason": str(e)})
        
        # 백그라운드 스레드에서 에이전트 실행
        thread = threading.Thread(target=run_agent)
        thread.start()
        
        try:
            # 시작 이벤트 발생
            yield {"type": "start"}
            
            start_time = time.time()
            
            while True:
                try:
                    event = self.event_queue.get(timeout=1.0)
                    yield event
                    
                    # 완료 또는 강제 중단 시 종료
                    if event.get("type") in ("complete", "force_stop"):
                        break
                        
                except queue.Empty:
                    if not thread.is_alive():
                        break
                    
                    # 타임아웃 처리 (60초)
                    if time.time() - start_time > 300:
                        yield {"type": "force_stop", "reason": "Timeout"}
                        break
                    
                    continue
        
        finally:
            thread.join()
            # 남은 이벤트들 정리
            while not self.event_queue.empty():
                try:
                    self.event_queue.get_nowait()
                except queue.Empty:
                    break
    
    def get_ui_state(self) -> StreamlitUIState:
        """현재 UI 상태를 반환합니다
        
        이 메서드는 Streamlit 프론트엔드에서 필수로 요구됩니다.
        """
        return self.ui_state
    
    def enable_debug_mode(self, enabled: bool = True):
        """디버그 모드를 토글합니다."""
        for handler in self.event_registry._handlers:
            if isinstance(handler, DebugHandler):
                handler.debug_enabled = enabled
                break