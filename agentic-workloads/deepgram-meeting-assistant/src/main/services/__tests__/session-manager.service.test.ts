/**
 * SessionManagerService Tests
 * 
 * Kent Beck 스타일:
 * - 한 번에 한 가지만 테스트
 * - 행동(behavior)을 테스트, 구현을 테스트하지 않음
 * - Arrange-Act-Assert 패턴
 * - 테스트 이름이 문서 역할
 */

import { describe, expect, it, beforeEach, vi } from 'vitest';
import { sessionManager } from '../session-manager.service';
import { SentenceBufferService } from '../sentence-buffer.service';

describe('SessionManagerService', () => {
  beforeEach(() => {
    // 각 테스트 전 세션 초기화
    sessionManager.resetSession();
  });

  describe('세션 생성', () => {
    it('새 세션을 생성하면 기본값이 설정된다', () => {
      // Arrange
      const params = {
        meetingId: 'meeting-123',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      };

      // Act
      const session = sessionManager.createSession(params);

      // Assert
      expect(session.meetingId).toBe('meeting-123');
      expect(session.meetingType).toBe('client');
      expect(session.language).toBe('ko-KR');
      expect(session.recentSentences).toEqual([]);
      expect(session.correctedCount).toBe(0);
      expect(session.titleGenerated).toBe(false);
      expect(session.prepData).toBeNull();
    });

    it('SentenceBuffer를 제공하면 해당 버퍼가 사용된다', () => {
      // Arrange
      const customBuffer = new SentenceBufferService('en-US');
      const params = {
        meetingId: 'meeting-456',
        meetingType: 'english' as const,
        language: 'en-US' as const,
        sentenceBuffer: customBuffer,
      };

      // Act
      const session = sessionManager.createSession(params);

      // Assert
      expect(session.sentenceBuffer).toBe(customBuffer);
    });

    it('기존 세션이 있으면 새 세션으로 덮어쓴다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'first-meeting',
        meetingType: 'weekly' as const,
        language: 'ko-KR' as const,
      });

      // Act
      const newSession = sessionManager.createSession({
        meetingId: 'second-meeting',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });

      // Assert
      expect(sessionManager.getMeetingId()).toBe('second-meeting');
      expect(newSession.meetingType).toBe('client');
    });
  });

  describe('세션 조회', () => {
    it('세션이 없으면 null을 반환한다', () => {
      // Act & Assert
      expect(sessionManager.getSession()).toBeNull();
      expect(sessionManager.hasActiveSession()).toBe(false);
      expect(sessionManager.getMeetingId()).toBeNull();
    });

    it('세션이 있으면 해당 세션을 반환한다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'active-meeting',
        meetingType: 'interview' as const,
        language: 'ko-KR' as const,
      });

      // Act & Assert
      expect(sessionManager.hasActiveSession()).toBe(true);
      expect(sessionManager.getMeetingId()).toBe('active-meeting');
    });
  });

  describe('최근 문장 관리', () => {
    it('문장을 추가하면 목록에 저장된다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'test',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });

      // Act
      sessionManager.addRecentSentence('[Speaker] 첫 번째 문장');
      sessionManager.addRecentSentence('[Speaker] 두 번째 문장');

      // Assert
      const sentences = sessionManager.getRecentSentences();
      expect(sentences).toHaveLength(2);
      expect(sentences[0]).toBe('[Speaker] 첫 번째 문장');
      expect(sentences[1]).toBe('[Speaker] 두 번째 문장');
    });

    it('최대 개수를 초과하면 가장 오래된 문장이 제거된다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'test',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });

      // Act - 최대 3개로 제한하고 4개 추가
      for (let i = 1; i <= 4; i++) {
        sessionManager.addRecentSentence(`문장 ${i}`, 3);
      }

      // Assert
      const sentences = sessionManager.getRecentSentences();
      expect(sentences).toHaveLength(3);
      expect(sentences[0]).toBe('문장 2');
      expect(sentences[2]).toBe('문장 4');
    });

    it('limit을 지정하면 해당 개수만큼만 반환한다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'test',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });
      for (let i = 1; i <= 5; i++) {
        sessionManager.addRecentSentence(`문장 ${i}`);
      }

      // Act
      const lastTwo = sessionManager.getRecentSentences(2);

      // Assert
      expect(lastTwo).toHaveLength(2);
      expect(lastTwo[0]).toBe('문장 4');
      expect(lastTwo[1]).toBe('문장 5');
    });
  });

  describe('교정 카운트 관리', () => {
    it('교정 카운트를 증가시키면 새 값을 반환한다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'test',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });

      // Act & Assert
      expect(sessionManager.incrementCorrectedCount()).toBe(1);
      expect(sessionManager.incrementCorrectedCount()).toBe(2);
      expect(sessionManager.getCorrectedCount()).toBe(2);
    });

    it('세션이 없으면 카운트가 0이다', () => {
      // Act & Assert
      expect(sessionManager.getCorrectedCount()).toBe(0);
      expect(sessionManager.incrementCorrectedCount()).toBe(0);
    });
  });

  describe('제목 생성 상태 관리', () => {
    it('초기 상태에서 제목은 생성되지 않은 상태다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'test',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });

      // Act & Assert
      expect(sessionManager.isTitleGenerated()).toBe(false);
    });

    it('제목 생성 완료를 표시할 수 있다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'test',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });

      // Act
      sessionManager.setTitleGenerated(true);

      // Assert
      expect(sessionManager.isTitleGenerated()).toBe(true);
    });
  });

  describe('세션 초기화', () => {
    it('resetSession은 세션을 즉시 null로 만든다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'test',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });

      // Act
      sessionManager.resetSession();

      // Assert
      expect(sessionManager.hasActiveSession()).toBe(false);
      expect(sessionManager.getSession()).toBeNull();
    });
  });

  describe('session backend abstraction', () => {
    beforeEach(() => {
      sessionManager.resetSession();
    });

    it('stores a backend and its kind on the session', () => {
      const fakeBackend: any = { kind: 'pipecat', stopStreaming: vi.fn() };
      sessionManager.createSession({
        meetingId: 'm1',
        meetingType: 'agentic',
        language: 'ko-KR',
        backend: fakeBackend,
        backendKind: 'pipecat',
      });
      const session = sessionManager.getSession();
      expect(session?.backend).toBe(fakeBackend);
      expect(session?.backendKind).toBe('pipecat');
    });

    it('clearSession calls backend.stopStreaming', async () => {
      const stop = vi.fn().mockResolvedValue(undefined);
      const fakeBackend: any = { kind: 'aws', stopStreaming: stop };
      sessionManager.createSession({
        meetingId: 'm1', meetingType: 'weekly', language: 'ko-KR',
        backend: fakeBackend, backendKind: 'aws',
      });
      await sessionManager.clearSession();
      expect(stop).toHaveBeenCalledOnce();
    });
  });

  describe('PrepData 관리', () => {
    it('prepData를 설정하고 조회할 수 있다', () => {
      // Arrange
      sessionManager.createSession({
        meetingId: 'test',
        meetingType: 'client' as const,
        language: 'ko-KR' as const,
      });
      const prepData = {
        company: 'Test Company',
        meetingDate: '2024-01-01',
        meetingTopic: 'Test Topic',
        attendees: 'Alice, Bob',
        note: 'Test note',
        selectedOpportunity: null,
        tasks: [],
      };

      // Act
      sessionManager.setPrepData(prepData);

      // Assert
      const session = sessionManager.getSession();
      expect(session?.prepData).toEqual(prepData);
    });
  });
});
