/**
 * MeetingCorrectionService Tests
 * 
 * Kent Beck 스타일:
 * - 순수 함수부터 테스트
 * - 외부 의존성은 격리하여 단위 테스트
 * - 비즈니스 로직의 정확성 검증
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import type { MeetingDetail, CorrectedSentence, TranscriptionSegment } from '../../../shared/types';

// Electron mock
vi.mock('electron', () => ({
  BrowserWindow: {
    getAllWindows: vi.fn(() => []),
  },
  app: {
    isPackaged: false,
    getVersion: vi.fn().mockReturnValue('1.0.0'),
  },
}));

// uuid mock
vi.mock('uuid', () => ({
  v4: vi.fn(() => 'test-uuid-123'),
}));

// meeting-prep-format mock
vi.mock('../../ipc/meeting-prep-format', () => ({
  formatMeetingPrepAsSegment: vi.fn((data) => `<meeting_context>${JSON.stringify(data)}</meeting_context>`),
  isMeetingPrepDataValid: vi.fn((data) => !!(data?.company || data?.meetingTopic)),
}));

describe('MeetingCorrectionService', () => {
  let meetingCorrectionService: typeof import('../meeting-correction.service').meetingCorrectionService;

  beforeEach(async () => {
    vi.resetModules();
    const module = await import('../meeting-correction.service');
    meetingCorrectionService = module.meetingCorrectionService;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('컨텍스트 빌드', () => {
    it('교정된 문장이 있으면 해당 문장들로 컨텍스트를 구성한다', () => {
      // Arrange
      const meeting: MeetingDetail = {
        id: 'meeting-123',
        type: 'client',
        title: 'Test Meeting',
        status: 'completed',
        language: 'ko-KR',
        startedAt: new Date(),
        duration: 3600,
        createdAt: new Date(),
        updatedAt: new Date(),
        segments: [
          {
            id: 's1',
            meetingId: 'meeting-123',
            resultId: 'r1',
            text: '원본 세그먼트',
            startTime: 0,
            endTime: 1,
            speakerLabel: 'Speaker 1',
            createdAt: new Date(),
          },
        ],
        correctedSentences: [
          {
            id: 'c1',
            meetingId: 'meeting-123',
            originalText: '원본',
            correctedText: '교정된 문장 1',
            translatedText: null,
            segmentIds: ['s1'],
            startTime: 0,
            endTime: 1,
            speakerLabel: 'Speaker 1',
            modelId: 'model',
            correctedAt: new Date(),
          },
          {
            id: 'c2',
            meetingId: 'meeting-123',
            originalText: '원본 2',
            correctedText: '교정된 문장 2',
            translatedText: null,
            segmentIds: ['s2'],
            startTime: 1,
            endTime: 2,
            speakerLabel: 'Speaker 2',
            modelId: 'model',
            correctedAt: new Date(),
          },
        ],
      };

      // Act
      const context = meetingCorrectionService.buildContextFromMeeting(meeting);

      // Assert
      expect(context).toHaveLength(2);
      expect(context[0]).toBe('[Speaker 1] 교정된 문장 1');
      expect(context[1]).toBe('[Speaker 2] 교정된 문장 2');
    });

    it('교정된 문장이 없으면 원본 세그먼트로 컨텍스트를 구성한다', () => {
      // Arrange
      const meeting: MeetingDetail = {
        id: 'meeting-456',
        type: 'weekly',
        title: 'Weekly Sync',
        status: 'recording',
        language: 'ko-KR',
        startedAt: new Date(),
        duration: 600,
        createdAt: new Date(),
        updatedAt: new Date(),
        segments: [
          {
            id: 's1',
            meetingId: 'meeting-456',
            resultId: 'r1',
            text: '첫 번째 세그먼트',
            startTime: 0,
            endTime: 1,
            speakerLabel: 'Alice',
            createdAt: new Date(),
          },
          {
            id: 's2',
            meetingId: 'meeting-456',
            resultId: 'r2',
            text: '두 번째 세그먼트',
            startTime: 1,
            endTime: 2,
            speakerLabel: null, // speakerLabel이 없는 경우
            createdAt: new Date(),
          },
        ],
        correctedSentences: [], // 교정된 문장 없음
      };

      // Act
      const context = meetingCorrectionService.buildContextFromMeeting(meeting);

      // Assert
      expect(context).toHaveLength(2);
      expect(context[0]).toBe('[Alice] 첫 번째 세그먼트');
      expect(context[1]).toBe('[Speaker] 두 번째 세그먼트'); // 기본값 'Speaker'
    });

    it('컨텍스트는 최대 10개로 제한된다', () => {
      // Arrange
      const correctedSentences: CorrectedSentence[] = [];
      for (let i = 0; i < 15; i++) {
        correctedSentences.push({
          id: `c${i}`,
          meetingId: 'meeting-789',
          originalText: `원본 ${i}`,
          correctedText: `교정 ${i}`,
          translatedText: null,
          segmentIds: [`s${i}`],
          startTime: i,
          endTime: i + 1,
          speakerLabel: 'Speaker',
          modelId: 'model',
          correctedAt: new Date(),
        });
      }

      const meeting: MeetingDetail = {
        id: 'meeting-789',
        type: 'client',
        title: 'Long Meeting',
        status: 'completed',
        language: 'ko-KR',
        startedAt: new Date(),
        duration: 7200,
        createdAt: new Date(),
        updatedAt: new Date(),
        segments: [],
        correctedSentences,
      };

      // Act
      const context = meetingCorrectionService.buildContextFromMeeting(meeting);

      // Assert - 마지막 10개만 반환
      expect(context).toHaveLength(10);
      expect(context[0]).toBe('[Speaker] 교정 5');
      expect(context[9]).toBe('[Speaker] 교정 14');
    });
  });

  describe('PrepData 컨텍스트 추가', () => {
    it('유효한 prepData가 있으면 컨텍스트 앞에 추가된다', () => {
      // Arrange
      const context = ['[Speaker] 기존 문장'];
      const prepData = {
        company: 'Test Corp',
        meetingTopic: '신규 프로젝트 논의',
        meetingDate: '',
        attendees: '',
        note: '',
        selectedOpportunity: null,
        tasks: [],
      };

      // Act
      const enriched = meetingCorrectionService.enrichContextWithPrepData(context, prepData);

      // Assert
      expect(enriched).toHaveLength(2);
      expect(enriched[0]).toContain('<meeting_context>');
      expect(enriched[1]).toBe('[Speaker] 기존 문장');
    });

    it('prepData가 없으면 원본 컨텍스트를 그대로 반환한다', () => {
      // Arrange
      const context = ['[Speaker] 문장 1', '[Speaker] 문장 2'];

      // Act
      const result = meetingCorrectionService.enrichContextWithPrepData(context, null);

      // Assert
      expect(result).toEqual(context);
      expect(result).toHaveLength(2);
    });

    it('빈 prepData는 추가되지 않는다', () => {
      // Arrange
      const context = ['[Speaker] 문장'];
      const emptyPrepData = {
        company: '',
        meetingTopic: '',
        meetingDate: '',
        attendees: '',
        note: '',
        selectedOpportunity: null,
        tasks: [],
      };

      // Act
      const result = meetingCorrectionService.enrichContextWithPrepData(context, emptyPrepData);

      // Assert
      expect(result).toEqual(context);
    });
  });
});
