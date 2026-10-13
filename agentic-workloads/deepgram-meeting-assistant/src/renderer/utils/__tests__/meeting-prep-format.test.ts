import { describe, expect, it } from 'vitest';
import {
  createEmptyMeetingPrepData,
  formatMeetingPrepAsSegment,
  isMeetingPrepDataValid,
} from '../meeting-prep-format';
import type { MeetingPrepData } from '@shared/types';

describe('meeting-prep-format', () => {
  it('flags empty prep data as invalid', () => {
    const empty = createEmptyMeetingPrepData();
    expect(isMeetingPrepDataValid(empty)).toBe(false);
  });

  it('formats meeting prep data into a structured segment', () => {
    const prepData: MeetingPrepData = {
      company: 'ACME',
      meetingDate: '2026-01-17',
      meetingTopic: 'Kickoff',
      attendees: 'Alice, Bob',
      note: 'Bring the deck',
      selectedOpportunity: {
        id: 'opp-1',
        name: 'Cloud Migration',
        stageName: 'Prospect',
        accountName: 'ACME',
        owner: { name: 'Dana' },
        closeDate: '2026-02-01',
      },
      tasks: [
        {
          id: 'task-1',
          subject: 'Send proposal',
          status: 'Open',
          activityDate: '2026-01-20',
        },
        {
          id: 'task-2',
          subject: 'Schedule follow-up',
          status: 'Done',
          activityDate: '2026-01-21',
        },
      ],
    };

    const output = formatMeetingPrepAsSegment(prepData);

    expect(output).toContain('<meeting_context>');
    expect(output).toContain('## 미팅 기본 정보');
    expect(output).toContain('- 고객사: ACME');
    expect(output).toContain('- 미팅 주제: Kickoff');
    expect(output).toContain('- 미팅 일자: 2026-01-17');
    expect(output).toContain('- 참석자: Alice, Bob');
    expect(output).toContain('- 메모: Bring the deck');
    expect(output).toContain('## Opportunity 정보');
    expect(output).toContain('- Opportunity 이름: Cloud Migration');
    expect(output).toContain('- 단계: Prospect');
    expect(output).toContain('- 고객사: ACME');
    expect(output).toContain('- 마감일: 2026-02-01');
    expect(output).toContain('- 담당자: Dana');
    expect(output).toContain('## 관련 Task 요약');
    expect(output).toContain('- 총 Task 수: 2건');
    expect(output).toContain('Open: 1건');
    expect(output).toContain('Done: 1건');
    expect(output).toContain('- Task 상세:');
    expect(output).toContain('1. [Open] Send proposal (예정일: 2026-01-20)');
    expect(output).toContain('2. [Done] Schedule follow-up (예정일: 2026-01-21)');
    expect(output).toContain('</meeting_context>');
  });
});
