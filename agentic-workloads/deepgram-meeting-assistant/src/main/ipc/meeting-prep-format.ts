/**
 * 미팅 준비 정보 포맷팅 유틸리티 (Main Process용)
 * MeetingPrepData를 LLM 프롬프트용 구조화된 텍스트로 변환
 * Requirements: 8.5
 */

import type { MeetingPrepData, OpportunityInfo, TaskInfo } from '../../shared/types/meeting-prep';

/**
 * 미팅 기본 정보를 LLM 프롬프트용 구조화된 텍스트로 변환
 */
const formatBasicInfo = (prepData: MeetingPrepData): string[] => {
  const lines: string[] = [];

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
 */
const formatOpportunityInfo = (opportunity: OpportunityInfo): string[] => {
  const lines: string[] = [];

  lines.push(`- Opportunity 이름: ${opportunity.name}`);
  lines.push(`- 단계: ${opportunity.stageName}`);
  if (opportunity.accountName) {
    lines.push(`- 고객사: ${opportunity.accountName}`);
  }
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
 */
const formatTaskSummary = (tasks: TaskInfo[]): string[] => {
  if (tasks.length === 0) {
    return [];
  }

  const lines: string[] = [];
  
  const statusCounts: Record<string, number> = {};
  tasks.forEach((task) => {
    const status = task.status || '미지정';
    statusCounts[status] = (statusCounts[status] || 0) + 1;
  });

  lines.push(`- 총 Task 수: ${tasks.length}건`);
  
  const statusSummary = Object.entries(statusCounts)
    .map(([status, count]) => `${status}: ${count}건`)
    .join(', ');
  lines.push(`- 상태별 현황: ${statusSummary}`);

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
 */
export const formatMeetingPrepAsSegment = (prepData: MeetingPrepData): string => {
  const sections: string[] = [];

  sections.push('<meeting_context>');
  sections.push('# 미팅 준비 정보');
  sections.push('');
  sections.push('이 정보는 미팅 시작 전 준비된 컨텍스트입니다. 미팅 내용을 이해하고 요약할 때 참고하세요.');
  sections.push('');

  const basicInfo = formatBasicInfo(prepData);
  if (basicInfo.length > 0) {
    sections.push('## 미팅 기본 정보');
    sections.push(...basicInfo);
    sections.push('');
  }

  if (prepData.selectedOpportunity) {
    sections.push('## Opportunity 정보');
    sections.push(...formatOpportunityInfo(prepData.selectedOpportunity));
    sections.push('');
  }

  if (prepData.tasks.length > 0) {
    sections.push('## 관련 Task 요약');
    sections.push(...formatTaskSummary(prepData.tasks));
    sections.push('');
  }

  sections.push('</meeting_context>');

  return sections.join('\n');
};

/**
 * 미팅 준비 데이터가 유효한지 확인
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
