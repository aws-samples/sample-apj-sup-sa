/**
 * Meeting Correction Service
 * 
 * 문장 교정, 번역, 제목 생성 관련 비즈니스 로직을 담당하는 서비스입니다.
 * 
 * ORCH-002, ORCH-015: 교정 및 요약 로직 분리
 */

import { BrowserWindow } from 'electron';
import { v4 as uuidv4 } from 'uuid';
import type {
  MeetingType,
  MeetingDetail,
} from '../../shared/types/meeting';
import type { TranscribeLanguage } from '../../shared/types/settings';
import type { CorrectedSentence } from '../../shared/types/transcription';
import type { CompletedSentence } from '../../shared/types/transcription';
import type { MeetingPrepData } from '../../shared/types/meeting-prep';
import { IPC_CHANNELS } from '../../shared/constants/ipc-channels';
// 순환 의존성 방지: barrel(index.ts) 대신 직접 모듈에서 import
import { BedrockService } from './bedrock.service';
import { DatabaseService } from './database.service';
import { sessionManager } from './session-manager.service';
import { formatMeetingPrepAsSegment, isMeetingPrepDataValid } from '../ipc/meeting-prep-format';
import { createLogger } from './logger.service';

const log = createLogger('meeting-correction');

/**
 * 교정 결과
 */
export interface CorrectionResult {
  correctedText: string;
  translatedText: string | null;
}

/**
 * 제목 생성 기준 상수
 */
const TITLE_GENERATION_THRESHOLD = 5;
const CONTEXT_SENTENCE_LIMIT = 10;

/**
 * 회의 교정 서비스
 */
class MeetingCorrectionService {
  private databaseService: DatabaseService | null = null;

  /**
   * 렌더러에 메시지를 전송합니다.
   */
  private sendToRenderer(channel: string, data: unknown): void {
    const windows = BrowserWindow.getAllWindows();
    if (windows.length > 0) {
      windows[0].webContents.send(channel, data);
    }
  }

  /**
   * DatabaseService 인스턴스를 반환합니다.
   */
  ensureDatabase(): DatabaseService {
    if (!this.databaseService) {
      this.databaseService = new DatabaseService();
    }
    return this.databaseService;
  }

  /**
   * DatabaseService를 설정합니다 (외부 주입용).
   */
  setDatabase(db: DatabaseService): void {
    this.databaseService = db;
  }

  /**
   * 미팅에서 컨텍스트 라인을 추출합니다.
   */
  buildContextFromMeeting(meeting: MeetingDetail): string[] {
    const lines = meeting.correctedSentences.length > 0
      ? meeting.correctedSentences.map(
          (sentence) => `[${sentence.speakerLabel || 'Speaker'}] ${sentence.correctedText}`
        )
      : meeting.segments.map(
          (segment) => `[${segment.speakerLabel || 'Speaker'}] ${segment.text}`
        );

    return lines.slice(-CONTEXT_SENTENCE_LIMIT);
  }

  /**
   * 미팅 ID로 컨텍스트 라인을 가져옵니다.
   * 활성 세션이 있으면 세션의 최근 문장을 사용합니다.
   */
  getContextLinesForMeeting(meetingId: string): string[] {
    const session = sessionManager.getSession();
    if (session?.meetingId === meetingId && session.recentSentences.length > 0) {
      return session.recentSentences.slice(-CONTEXT_SENTENCE_LIMIT);
    }

    const meeting = this.ensureDatabase().getMeeting(meetingId);
    if (!meeting) {
      return [];
    }

    return this.buildContextFromMeeting(meeting);
  }

  /**
   * 컨텍스트에 미팅 준비 정보를 추가합니다.
   */
  enrichContextWithPrepData(context: string[], prepData?: MeetingPrepData | null): string[] {
    if (prepData && isMeetingPrepDataValid(prepData)) {
      const prepContext = formatMeetingPrepAsSegment(prepData);
      return [prepContext, ...context];
    }
    return context;
  }

  /**
   * 문장을 교정하고 번역합니다. (범용 메서드)
   * sourceLanguage === targetLanguage인 경우, 번역을 건너뜁니다.
   */
  private async correctAndTranslateGeneric(
    originalText: string,
    sourceLanguage: TranscribeLanguage,
    targetLanguage: TranscribeLanguage,
    correctionService: BedrockService,
    translationService: BedrockService | null,
    enrichedContext: string[]
  ): Promise<CorrectionResult> {
    // Skip translation if source === target
    if (sourceLanguage === targetLanguage) {
      const correctedText = await correctionService.correctTranscription(
        originalText,
        sourceLanguage,
        enrichedContext
      );
      return { correctedText, translatedText: null };
    }

    const useSameModel =
      translationService &&
      correctionService.getModelId() === translationService.getModelId();

    if (useSameModel) {
      try {
        const result = await correctionService.correctAndTranslateGeneric(
          originalText,
          sourceLanguage,
          targetLanguage,
          enrichedContext
        );
        const combinedCorrected = result.correctedText?.trim();
        const combinedTranslated = result.translatedText?.trim();

        if (combinedCorrected && combinedTranslated) {
          return {
            correctedText: combinedCorrected,
            translatedText: combinedTranslated,
          };
        }
      } catch (error) {
        log.error({ err: error }, 'Bedrock correctAndTranslateGeneric failed');
      }
    }

    // Fallback to individual API calls
    const correctedText = await correctionService.correctTranscription(
      originalText,
      sourceLanguage,
      enrichedContext
    );

    let translatedText: string | null = null;
    if (translationService) {
      try {
        translatedText = await translationService.translate(
          correctedText,
          sourceLanguage,
          targetLanguage,
          enrichedContext
        );
      } catch (error) {
        log.error({ err: error }, 'Bedrock translation failed');
        translatedText = null;
      }
    }

    return { correctedText, translatedText };
  }

  /**
   * 문장을 교정하고 저장합니다.
   */
  async correctAndSaveSentence(
    sentence: CompletedSentence,
    meetingId: string,
    correctionService: BedrockService,
    translationService: BedrockService | null,
    sourceLanguage: TranscribeLanguage,
    targetLanguage: TranscribeLanguage,
    context: string[],
    prepData?: MeetingPrepData | null
  ): Promise<string> {
    const enrichedContext = this.enrichContextWithPrepData(context, prepData);

    try {
      const result = await this.correctAndTranslateGeneric(
        sentence.originalText,
        sourceLanguage,
        targetLanguage,
        correctionService,
        translationService,
        enrichedContext
      );

      const correctedSentence: Omit<CorrectedSentence, 'correctedAt'> = {
        id: uuidv4(),
        meetingId,
        originalText: sentence.originalText,
        correctedText: result.correctedText,
        translatedText: result.translatedText,
        segmentIds: sentence.segmentIds,
        startTime: sentence.startTime,
        endTime: sentence.endTime,
        speakerLabel: sentence.speakerLabel,
        modelId: correctionService.getModelId(),
      };

      this.ensureDatabase().saveCorrectedSentence(correctedSentence);

      this.sendToRenderer(IPC_CHANNELS.TRANSCRIPTION_CORRECTED, {
        id: correctedSentence.id,
        originalText: sentence.originalText,
        correctedText: result.correctedText,
        translatedText: result.translatedText,
        segmentIds: sentence.segmentIds,
        speakerLabel: sentence.speakerLabel,
        startTime: sentence.startTime,
        endTime: sentence.endTime,
      });

      return result.correctedText;
    } catch (error) {
      log.error({ err: error }, 'Bedrock correction failed');
      return sentence.originalText;
    }
  }

  /**
   * 미팅 제목을 생성하고 업데이트합니다.
   */
  async generateAndUpdateTitle(
    meetingId: string,
    meetingType: MeetingType,
    recentSentences: string[],
    correctionService: BedrockService
  ): Promise<void> {
    const session = sessionManager.getSession();
    
    // 조건 확인
    if (session?.titleGenerated) {
      return;
    }
    
    const correctedCount = session?.correctedCount ?? 0;
    if (correctedCount < TITLE_GENERATION_THRESHOLD) {
      return;
    }

    const db = this.ensureDatabase();
    const meeting = db.getMeeting(meetingId);
    if (!meeting) return;

    const isUntitled =
      !meeting.title ||
      meeting.title.startsWith('Meeting ') ||
      meeting.title.startsWith('Untitled');

    if (!isUntitled) {
      sessionManager.setTitleGenerated(true);
      return;
    }

    try {
      sessionManager.setTitleGenerated(true);
      const title = await correctionService.generateMeetingTitle(
        meetingType,
        recentSentences
      );

      db.updateMeetingTitle(meetingId, title);
      this.sendToRenderer(IPC_CHANNELS.MEETING_TITLE_UPDATED, {
        meetingId,
        title,
      });
    } catch (error) {
      log.error({ err: error }, 'Failed to generate meeting title');
    }
  }

  /**
   * 교정된 문장을 세션에 추가하고 제목 생성을 시도합니다.
   */
  async processCorrectedSentence(
    sentence: CompletedSentence,
    correctionService: BedrockService
  ): Promise<void> {
    const session = sessionManager.getSession();
    if (!session) return;

    const correctedText = await this.correctAndSaveSentence(
      sentence,
      session.meetingId,
      correctionService,
      session.translationService,
      session.language,
      session.targetLanguage,
      session.recentSentences,
      session.prepData
    );

    const speaker = sentence.speakerLabel || 'Speaker';
    sessionManager.addRecentSentence(`[${speaker}] ${correctedText}`);
    sessionManager.incrementCorrectedCount();

    await this.generateAndUpdateTitle(
      session.meetingId,
      session.meetingType,
      sessionManager.getRecentSentences(),
      correctionService
    );
  }

  /**
   * DatabaseService 연결을 종료합니다.
   */
  closeDatabase(): void {
    if (this.databaseService) {
      this.databaseService.close();
      this.databaseService = null;
    }
  }
}

// 싱글톤 인스턴스 export
export const meetingCorrectionService = new MeetingCorrectionService();
