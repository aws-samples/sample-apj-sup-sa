/**
 * 미팅 준비 정보 포맷팅 유틸리티
 * MeetingPrepData를 트랜스크립트 세그먼트 형식의 텍스트로 변환
 * LLM 프롬프트에 포함될 수 있는 구조화된 텍스트 형식 제공
 * Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
 */

import type { MeetingPrepData, OpportunityInfo, TaskInfo } from '@shared/types';

/**
 * 미팅 기본 정보를 LLM 프롬프트용 구조화된 텍스트로 변환
 * Requirements: 8.2 - 고객사명, 미팅 주제, 미팅 일자, 참석자 정보 포함
 */
const formatBasicInfo = (prepData: MeetingPrepData): string[] => {
  const lines: string[] = [];

  // Requirements 8.2: 고객사명, 미팅 주제, 미팅 일자, 참석자 정보를 포함
  if (prepData.company) {
    lines.push(`- 고객사: ${prepData.company}`);
  }
  if (prepData.meetingTopic) {
    lines.push(`- 미팅 주제: ${prepData.meetingTopic}`);
  }
  if (prepData.meetingDate) {
    lines.push(`- 미팅 일자: ${prepData.meetingDate}`);
  }
  if (prepData.attendees) {
    lines.push(`- 참석자: ${prepData.attendees}`);
  }
  if (prepData.note) {
    lines.push(`- 메모: ${prepData.note}`);
  }

  return lines;
};

/**
 * Opportunity 정보를 LLM 프롬프트용 구조화된 텍스트로 변환
 * Requirements: 8.3 - Opportunity 이름, 단계, 고객사명 포함
 */
const formatOpportunityInfo = (opportunity: OpportunityInfo): string[] => {
  const lines: string[] = [];

  // Requirements 8.3: Opportunity 이름, 단계, 고객사명을 포함
  lines.push(`- Opportunity 이름: ${opportunity.name}`);
  lines.push(`- 단계: ${opportunity.stageName}`);
  if (opportunity.accountName) {
    lines.push(`- 고객사: ${opportunity.accountName}`);
  }
  // 추가 컨텍스트 정보 (LLM에 유용한 정보)
  if (opportunity.closeDate) {
    lines.push(`- 마감일: ${opportunity.closeDate}`);
  }
  if (opportunity.owner?.name) {
    lines.push(`- 담당자: ${opportunity.owner.name}`);
  }

  return lines;
};

/**
 * Task 목록을 LLM 프롬프트용 구조화된 요약 텍스트로 변환
 * Requirements: 8.4 - Task 목록 요약 포함
 */
const formatTaskSummary = (tasks: TaskInfo[]): string[] => {
  if (tasks.length === 0) {
    return [];
  }

  const lines: string[] = [];
  
  // Task 상태별 카운트 계산
  const statusCounts: Record<string, number> = {};
  tasks.forEach((task) => {
    const status = task.status || '미지정';
    statusCounts[status] = (statusCounts[status] || 0) + 1;
  });

  // 요약 정보
  lines.push(`- 총 Task 수: ${tasks.length}건`);
  
  // 상태별 요약
  const statusSummary = Object.entries(statusCounts)
    .map(([status, count]) => `${status}: ${count}건`)
    .join(', ');
  lines.push(`- 상태별 현황: ${statusSummary}`);

  // 개별 Task 목록 (LLM이 상세 컨텍스트로 활용)
  lines.push('- Task 상세:');
  tasks.forEach((task, index) => {
    const statusText = task.status ? `[${task.status}]` : '[상태 미지정]';
    const dateText = task.activityDate ? ` (예정일: ${task.activityDate})` : '';
    lines.push(`  ${index + 1}. ${statusText} ${task.subject}${dateText}`);
  });

  return lines;
};

/**
 * MeetingPrepData를 LLM 프롬프트용 구조화된 텍스트로 변환
 * 트랜스크립트 세그먼트 형식으로 표시될 수 있는 형태
 * Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
 *
 * 이 함수는 미팅 준비 정보를 LLM이 컨텍스트로 활용할 수 있도록
 * 구조화된 텍스트 형식으로 변환합니다.
 *
 * @param prepData 미팅 준비 데이터
 * @returns LLM 프롬프트에 포함될 수 있는 구조화된 텍스트 문자열
 */
export const formatMeetingPrepAsSegment = (prepData: MeetingPrepData): string => {
  const sections: string[] = [];

  // LLM 프롬프트용 헤더 (Requirements: 8.5)
  sections.push('<meeting_context>');
  sections.push('# 미팅 준비 정보');
  sections.push('');
  sections.push('이 정보는 미팅 시작 전 준비된 컨텍스트입니다. 미팅 내용을 이해하고 요약할 때 참고하세요.');
  sections.push('');

  // 미팅 기본 정보 (Requirements: 8.2)
  const basicInfo = formatBasicInfo(prepData);
  if (basicInfo.length > 0) {
    sections.push('## 미팅 기본 정보');
    sections.push(...basicInfo);
    sections.push('');
  }

  // Opportunity 정보 (Requirements: 8.3)
  if (prepData.selectedOpportunity) {
    sections.push('## Opportunity 정보');
    sections.push(...formatOpportunityInfo(prepData.selectedOpportunity));
    sections.push('');
  }

  // Task 목록 요약 (Requirements: 8.4)
  if (prepData.tasks.length > 0) {
    sections.push('## 관련 Task 요약');
    sections.push(...formatTaskSummary(prepData.tasks));
    sections.push('');
  }

  // LLM 프롬프트용 푸터 (Requirements: 8.5)
  sections.push('</meeting_context>');

  return sections.join('\n');
};

/**
 * 미팅 준비 데이터가 유효한지 확인
 * 최소한 하나의 정보가 입력되어 있어야 함
 *
 * @param prepData 미팅 준비 데이터
 * @returns 유효 여부
 */
export const isMeetingPrepDataValid = (prepData: MeetingPrepData): boolean => {
  return !!(
    prepData.company ||
    prepData.meetingTopic ||
    prepData.meetingDate ||
    prepData.attendees ||
    prepData.note ||
    prepData.selectedOpportunity ||
    prepData.tasks.length > 0
  );
};

/**
 * 빈 MeetingPrepData 객체 생성
 *
 * @returns 초기화된 MeetingPrepData
 */
export const createEmptyMeetingPrepData = (): MeetingPrepData => ({
  company: '',
  meetingDate: '',
  meetingTopic: '',
  attendees: '',
  note: '',
  selectedOpportunity: null,
  tasks: [],
});
