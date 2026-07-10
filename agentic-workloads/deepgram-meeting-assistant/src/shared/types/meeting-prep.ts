/**
 * 미팅 준비 모달 관련 타입 정의
 * Requirements: 3.1-3.5, 6.1, 6.5
 */

/**
 * Opportunity 정보 인터페이스
 * MCP 서버에서 반환하는 Opportunity 정보
 * Requirements: 6.1
 */
export interface OpportunityInfo {
  id: string;
  name: string;
  stageName: string;
  accountName: string;
  /** Account 정보 (MCP 서버 응답에 포함될 수 있음) */
  account?: {
    id?: string;
    name?: string;
  };
  /** Owner 정보 (MCP 서버 응답에 포함될 수 있음) */
  owner?: {
    id?: string;
    name?: string;
    alias?: string;
  };
  /** Close Date (MCP 서버 응답에 포함될 수 있음) */
  closeDate?: string;
  /** MCP 서버 응답의 추가 필드들 */
  [key: string]: unknown;
}

/**
 * Task 정보 인터페이스
 * Opportunity에 연결된 Task 정보
 * Requirements: 6.5
 */
export interface TaskInfo {
  id: string;
  subject: string;
  status: string;
  activityDate: string;
  /** MCP 서버 응답의 추가 필드들 */
  [key: string]: unknown;
}

/**
 * 미팅 준비 데이터 인터페이스
 * 모달에서 수집하는 모든 미팅 준비 정보
 * Requirements: 3.1-3.5
 */
export interface MeetingPrepData {
  /** 고객사명 (Requirements: 3.1) */
  company: string;
  /** 미팅 일자 (Requirements: 3.2) */
  meetingDate: string;
  /** 미팅 주제 (Requirements: 3.3) */
  meetingTopic: string;
  /** 참석자 정보 (Requirements: 3.4) */
  attendees: string;
  /** 메모 (Requirements: 3.5) */
  note: string;
  /** 선택된 Opportunity 정보 */
  selectedOpportunity: OpportunityInfo | null;
  /** Task 목록 */
  tasks: TaskInfo[];
}

/**
 * Opportunity 검색 결과 인터페이스
 * search_opportunities 도구의 응답 형식
 */
export interface OpportunitySearchResult {
  opportunities: OpportunityInfo[];
  totalCount: number;
  hasNextPage: boolean;
  cursor: string | null;
}

/**
 * Task 검색 결과 인터페이스
 * list_user_tasks 도구의 응답 형식
 */
export interface TaskSearchResult {
  tasks: TaskInfo[];
  pageInfo: {
    hasNextPage: boolean;
    cursor: string | null;
  };
}
