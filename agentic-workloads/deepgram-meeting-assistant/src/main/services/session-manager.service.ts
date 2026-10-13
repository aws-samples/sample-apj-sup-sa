/**
 * Session Manager Service
 * 
 * 회의 세션 상태를 관리하는 서비스입니다.
 * 전역 상태 대신 클래스로 캡슐화하여 상태 관리를 중앙화합니다.
 * 
 * ORCH-020: Global Session State → 세션 관리 클래스로 캡슐화
 */

import type { MeetingType } from '../../shared/types/meeting';
import type { TranscribeLanguage } from '../../shared/types/settings';
import type { MeetingPrepData } from '../../shared/types/meeting-prep';
// 순환 의존성 방지: barrel(index.ts) 대신 직접 모듈에서 import
import { TranscribeService } from './transcribe.service';
import { BedrockService } from './bedrock.service';
import { SentenceBufferService } from './sentence-buffer.service';
import type { StreamingBackend, StreamingBackendKind } from './streaming-backend';

/**
 * 회의 세션 상태 인터페이스
 */
export interface MeetingSessionState {
  meetingId: string;
  meetingType: MeetingType;
  language: TranscribeLanguage;  // Source language (what's being spoken)
  targetLanguage: TranscribeLanguage;  // Target language for translation
  backend: StreamingBackend | null;
  backendKind: StreamingBackendKind | null;
  transcribeService: TranscribeService | null; // @deprecated AWS 경로 하위호환
  correctionService: BedrockService | null;
  translationService: BedrockService | null;
  sentenceBuffer: SentenceBufferService;
  recentSentences: string[];
  correctedCount: number;
  titleGenerated: boolean;
  prepData: MeetingPrepData | null;
  transcribeTimeOffsetSec: number;
  lastSegmentEndTimeSec: number;
}

/**
 * 세션 생성을 위한 초기 파라미터
 */
export interface CreateSessionParams {
  meetingId: string;
  meetingType: MeetingType;
  language: TranscribeLanguage;  // Source language
  targetLanguage?: TranscribeLanguage;  // Target language (defaults to 'ko-KR')
  backend?: StreamingBackend | null;
  backendKind?: StreamingBackendKind | null;
  transcribeService?: TranscribeService | null;
  correctionService?: BedrockService | null;
  translationService?: BedrockService | null;
  sentenceBuffer?: SentenceBufferService;
  prepData?: MeetingPrepData | null;
}

/**
 * 세션 업데이트를 위한 부분 파라미터
 */
export type UpdateSessionParams = Partial<Omit<MeetingSessionState, 'meetingId'>>;

/**
 * 회의 세션 관리 서비스
 * 
 * 싱글톤 패턴으로 구현되어 앱 전체에서 하나의 세션 상태를 공유합니다.
 */
class SessionManagerService {
  private session: MeetingSessionState | null = null;

  /**
   * 현재 활성화된 세션을 반환합니다.
   */
  getSession(): MeetingSessionState | null {
    return this.session;
  }

  /**
   * 세션이 활성화되어 있는지 확인합니다.
   */
  hasActiveSession(): boolean {
    return this.session !== null;
  }

  /**
   * 현재 세션의 미팅 ID를 반환합니다.
   */
  getMeetingId(): string | null {
    return this.session?.meetingId ?? null;
  }

  /**
   * 새로운 세션을 생성합니다.
   * 기존 세션이 있으면 덮어씁니다.
   */
  createSession(params: CreateSessionParams): MeetingSessionState {
    this.session = {
      meetingId: params.meetingId,
      meetingType: params.meetingType,
      language: params.language,
      targetLanguage: params.targetLanguage ?? 'ko-KR',
      backend: params.backend ?? params.transcribeService ?? null,
      backendKind: params.backendKind ?? (params.transcribeService ? 'aws' : null),
      transcribeService: params.transcribeService ?? null,
      correctionService: params.correctionService ?? null,
      translationService: params.translationService ?? null,
      sentenceBuffer: params.sentenceBuffer ?? new SentenceBufferService(params.language),
      recentSentences: [],
      correctedCount: 0,
      titleGenerated: false,
      prepData: params.prepData ?? null,
      transcribeTimeOffsetSec: 0,
      lastSegmentEndTimeSec: 0,
    };
    return this.session;
  }

  /**
   * 기존 세션을 부분적으로 업데이트합니다.
   * 세션이 없으면 null을 반환합니다.
   */
  updateSession(params: UpdateSessionParams): MeetingSessionState | null {
    if (!this.session) {
      return null;
    }

    this.session = {
      ...this.session,
      ...params,
    };
    return this.session;
  }

  /**
   * 세션의 TranscribeService를 업데이트합니다.
   */
  setTranscribeService(service: TranscribeService | null): void {
    if (this.session) {
      this.session.transcribeService = service;
    }
  }

  /**
   * 세션의 BedrockService들을 업데이트합니다.
   */
  setBedrockServices(
    correctionService: BedrockService | null,
    translationService: BedrockService | null
  ): void {
    if (this.session) {
      this.session.correctionService = correctionService;
      this.session.translationService = translationService;
    }
  }

  /**
   * 세션의 미팅 준비 데이터를 업데이트합니다.
   */
  setPrepData(prepData: MeetingPrepData | null): void {
    if (this.session) {
      this.session.prepData = prepData;
    }
  }

  /**
   * 최근 문장 목록에 문장을 추가합니다.
   * 최대 개수를 초과하면 가장 오래된 문장을 제거합니다.
   */
  addRecentSentence(sentence: string, maxCount: number = 10): void {
    if (this.session) {
      this.session.recentSentences.push(sentence);
      if (this.session.recentSentences.length > maxCount) {
        this.session.recentSentences.shift();
      }
    }
  }

  /**
   * 최근 문장 목록을 반환합니다.
   */
  getRecentSentences(limit?: number): string[] {
    if (!this.session) {
      return [];
    }
    const sentences = this.session.recentSentences;
    return limit ? sentences.slice(-limit) : sentences;
  }

  /**
   * 교정된 문장 수를 증가시킵니다.
   */
  incrementCorrectedCount(): number {
    if (this.session) {
      this.session.correctedCount++;
      return this.session.correctedCount;
    }
    return 0;
  }

  /**
   * 교정된 문장 수를 반환합니다.
   */
  getCorrectedCount(): number {
    return this.session?.correctedCount ?? 0;
  }

  /**
   * 제목 생성 완료 여부를 설정합니다.
   */
  setTitleGenerated(generated: boolean): void {
    if (this.session) {
      this.session.titleGenerated = generated;
    }
  }

  /**
   * 제목이 생성되었는지 확인합니다.
   */
  isTitleGenerated(): boolean {
    return this.session?.titleGenerated ?? false;
  }

  /**
   * 현재 세션을 정리하고 null로 설정합니다.
   * TranscribeService가 있으면 스트리밍을 중지합니다.
   */
  async clearSession(): Promise<void> {
    if (this.session?.backend) {
      // cleanup 경로: stopStreaming이 degraded로 reject해도 세션 정리는 진행한다.
      try {
        await this.session.backend.stopStreaming();
      } catch {
        // 정리 중 종료 실패는 무시(이미 영속된 데이터는 보존됨).
      }
    }
    this.session = null;
  }

  /**
   * 세션을 즉시 null로 설정합니다.
   * 리소스 정리 없이 상태만 초기화합니다.
   */
  resetSession(): void {
    this.session = null;
  }
}

// 싱글톤 인스턴스 export
export const sessionManager = new SessionManagerService();
