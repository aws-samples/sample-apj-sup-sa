/**
 * Meeting IPC Handlers
 * 
 * 회의 관련 IPC 핸들러를 등록합니다.
 * 비즈니스 로직은 서비스로 위임하고, 핸들러는 입력 검증과 응답 반환만 담당합니다.
 * 
 * ORCH-002: Large Multi-Responsibility Handler → IPC 핸들러만 남기고 서비스로 위임
 */

import { ipcMain, BrowserWindow } from 'electron';
import { v4 as uuidv4 } from 'uuid';
import { z } from 'zod';
import { IPC_CHANNELS } from '../../shared/constants/ipc-channels';
import type {
  MeetingType,
  MeetingMetadata,
} from '../../shared/types/meeting';
import type { TranscribeLanguage } from '../../shared/types/settings';
import type { TranscriptionSegment } from '../../shared/types/transcription';
import type { MeetingPrepData } from '../../shared/types/meeting-prep';
import {
  sessionManager,
  meetingStreamingService,
  meetingCorrectionService,
  BedrockService,
  SentenceBufferService,
} from '../services';
import type { BedrockServiceConfig } from '../services';
import { getVocabularyService } from '../services/vocabulary.service';
import { PipecatBridgeService, type AssistantEvent } from '../services/pipecat-bridge.service';
import { base64ToBuffer } from '../utils/audio-converter';
import { formatMeetingPrepAsSegment, isMeetingPrepDataValid } from './meeting-prep-format';
import { createLogger } from '../services/logger.service';
import { rateLimiter, RATE_LIMIT_KEYS } from '../services/rate-limiter.service';

const log = createLogger('meeting-handlers');

const PIPECAT_SERVER_URL = process.env.PIPECAT_SERVER_URL ?? 'ws://localhost:9876';

// ============================================================================
// Zod Schemas for Input Validation
// ============================================================================

const MeetingCreateSchema = z.object({
  type: z.enum(['client', 'weekly', 'english', 'translated', 'interview', 'agentic']),
  language: z.enum(['ko-KR', 'en-US', 'ja-JP', 'zh-CN']),
  title: z.string().optional(),
});

const MeetingStartSchema = z.object({
  meetingId: z.string().uuid(),
  language: z.enum(['ko-KR', 'en-US', 'ja-JP', 'zh-CN']).optional(),
  targetLanguage: z.enum(['ko-KR', 'en-US', 'ja-JP', 'zh-CN']).optional(),
  vocabularyId: z.string().optional(),
});

const MeetingGetSchema = z.object({
  id: z.string().uuid(),
});

const MeetingListSchema = z.object({
  limit: z.number().int().min(1).max(100).optional(),
  offset: z.number().int().min(0).optional(),
});

const MeetingUpdateMetadataSchema = z.object({
  id: z.string().uuid(),
  metadata: z.record(z.string(), z.unknown()),
});

const MeetingUpdatePrepDataSchema = z.object({
  prepData: z.custom<MeetingPrepData>().nullable(),
});

const EnglishSuggestionsSchema = z.object({
  meetingId: z.string().uuid(),
  count: z.number().int().min(1).max(20).optional(),
});

const EnglishTranslateSchema = z.object({
  meetingId: z.string().uuid().nullish(),
  text: z.string(),
});

const InterviewSuggestionsSchema = z.object({
  meetingId: z.string().uuid(),
  lpIds: z.array(z.string()).min(1).max(2),
  count: z.number().int().min(1).max(20).optional(),
});

const SummaryGenerateSchema = z.object({
  meetingId: z.string().uuid(),
  prepData: z.custom<MeetingPrepData>().nullable().optional(),
});

const ConversationLogGenerateSchema = z.object({
  meetingId: z.string().uuid(),
});

const AudioChunkSchema = z.object({
  data: z.string(),
});

// ============================================================================
// Helper Functions
// ============================================================================

function sendToRenderer(channel: string, data: unknown): void {
  const windows = BrowserWindow.getAllWindows();
  if (windows.length > 0) {
    windows[0].webContents.send(channel, data);
  }
}

function handlePartialResult(text: string, speakerLabel: string | null): void {
  sendToRenderer(IPC_CHANNELS.TRANSCRIPTION_PARTIAL, {
    type: 'partial',
    text,
    speakerLabel,
  });
}

function handleTranscriptionError(error: Error): void {
  sendToRenderer(IPC_CHANNELS.TRANSCRIPTION_ERROR, {
    error: error.message,
  });
}

// 음성 어시스턴트 이벤트(start/text/audio/end)를 그대로 renderer로 포워딩.
// 오디오 재생/텍스트 표시는 renderer(useAssistant)에서 담당한다.
function handleAssistantEvent(event: AssistantEvent): void {
  sendToRenderer(IPC_CHANNELS.ASSISTANT_EVENT, event);
}

function createFinalResultHandler(): (segment: TranscriptionSegment) => Promise<void> {
  return async (segment: TranscriptionSegment) => {
    const session = sessionManager.getSession();
    const offset = session?.transcribeTimeOffsetSec ?? 0;
    const adjustedSegment = {
      ...segment,
      startTime: segment.startTime + offset,
      endTime: segment.endTime + offset,
    };

    const db = meetingCorrectionService.ensureDatabase();
    db.saveSegment(adjustedSegment);

    sendToRenderer(IPC_CHANNELS.TRANSCRIPTION_FINAL, {
      type: 'final',
      segment: adjustedSegment,
    });

    if (session) {
      const lastEnd = Math.max(session.lastSegmentEndTimeSec, adjustedSegment.endTime);
      sessionManager.updateSession({ lastSegmentEndTimeSec: lastEnd });
    }

    if (session?.correctionService) {
      const completedSentences = session.sentenceBuffer.addSegment({
        id: adjustedSegment.id,
        text: adjustedSegment.text,
        startTime: adjustedSegment.startTime,
        endTime: adjustedSegment.endTime,
        speakerLabel: adjustedSegment.speakerLabel,
      });

      for (const completedSentence of completedSentences) {
        await meetingCorrectionService.processCorrectedSentence(
          completedSentence,
          session.correctionService
        );
      }
    }
  };
}

async function handlePipecatCorrection(
  meetingId: string,
  resultId: string,
  original: string,
  corrected: string
): Promise<void> {
  const db = meetingCorrectionService.ensureDatabase();
  // 원본 segment의 id/시간/speaker를 한 번에 조회한다. startTime을 0으로 박으면
  // 교정 문장이 렌더러 시간순 정렬에서 맨 앞으로 쏠려 미교정 segment와 순서가 어긋난다.
  const segmentInfo = db.getSegmentInfoByResultId(meetingId, resultId);
  if (segmentInfo.ids.length === 0) {
    log.warn({ meetingId, resultId }, 'Orphan correction (no matching segment), dropping');
    return;
  }
  const correctedSentence = {
    id: uuidv4(),
    meetingId,
    originalText: original,
    correctedText: corrected,
    translatedText: null,
    segmentIds: segmentInfo.ids,
    startTime: segmentInfo.startTime,
    endTime: segmentInfo.endTime,
    speakerLabel: segmentInfo.speakerLabel,
    modelId: 'pipecat-bedrock',
  };
  db.saveCorrectedSentence(correctedSentence);
  sendToRenderer(IPC_CHANNELS.TRANSCRIPTION_CORRECTED, {
    id: correctedSentence.id,
    originalText: original,
    correctedText: corrected,
    translatedText: null,
    segmentIds: segmentInfo.ids,
    speakerLabel: segmentInfo.speakerLabel,
    startTime: segmentInfo.startTime,
    endTime: segmentInfo.endTime,
  });
}

// ============================================================================
// Public Functions
// ============================================================================

export function initializeDatabase(): void {
  meetingCorrectionService.ensureDatabase();
}

// ============================================================================
// Settings Types
// ============================================================================

interface TranscribeSettingsInput {
  languageCode: TranscribeLanguage;
  translationTargetLanguage: TranscribeLanguage;
  partialResultsStability: 'high' | 'medium' | 'low';
  enablePartialResultsStabilization: boolean;
  showSpeakerLabel: boolean;
  vocabularyName?: string;
}

interface BedrockSettingsInput {
  correctionModelId: string;
  translationModelId: string;
  summaryModelId: string;
  maxTokens: number;
  temperature: number;
  enableCorrection: boolean;
}

interface SettingsInput {
  transcribe: TranscribeSettingsInput;
  bedrock: BedrockSettingsInput;
}

interface CredentialsInput {
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
}

// ============================================================================
// Register Handlers
// ============================================================================

export function registerMeetingHandlers(
  getCredentials: () => Promise<CredentialsInput | null>,
  getSettings: () => Promise<SettingsInput>
): void {
  // Helper: Start streaming with services
  const startStreaming = async (
    meetingId: string,
    meetingType: MeetingType,
    settings: SettingsInput,
    credentials: CredentialsInput | null,
    languageOverride?: TranscribeLanguage
  ): Promise<void> => {
    if (meetingType === 'agentic') {
      const language = languageOverride ?? settings.transcribe.languageCode;
      const bridge = new PipecatBridgeService({
        url: PIPECAT_SERVER_URL,
        language,
        targetLanguage: settings.transcribe.translationTargetLanguage,
        vocabularyName: settings.transcribe.vocabularyName,
        enableCorrection: settings.bedrock.enableCorrection,
        onCorrection: (resultId, original, corrected) =>
          handlePipecatCorrection(meetingId, resultId, original, corrected),
        onAssistant: handleAssistantEvent,
      });
      sessionManager.updateSession({ backend: bridge, backendKind: 'pipecat' });
      await bridge.startStreaming(
        meetingId,
        handlePartialResult,
        createFinalResultHandler(),
        handleTranscriptionError
      );
      return;
    }

    if (!credentials) {
      throw new Error('AWS credentials not configured');
    }
    const session = meetingStreamingService.startStreaming(
      {
        meetingId,
        meetingType,
        credentials,
        transcribeSettings: settings.transcribe,
        bedrockSettings: settings.bedrock,
        languageOverride,
      },
      {
        onPartialResult: handlePartialResult,
        onFinalResult: createFinalResultHandler(),
        onError: handleTranscriptionError,
      }
    );
    if (session.transcribeService) {
      sessionManager.updateSession({
        backend: session.transcribeService,
        backendKind: 'aws',
      });
    }
  };

  // MEETING_CREATE
  ipcMain.handle(IPC_CHANNELS.MEETING_CREATE, async (_event, params: unknown) => {
    try {
      const validated = MeetingCreateSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }
      const { type, language, title } = validated.data;
      const meeting = meetingCorrectionService.ensureDatabase().createMeeting(type, language, title);
      return { success: true, meeting };
    } catch (error) {
      log.error({ err: error }, 'Failed to create meeting');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_START
  ipcMain.handle(IPC_CHANNELS.MEETING_START, async (_event, params: unknown) => {
    try {
      const validated = MeetingStartSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }
      const { meetingId, language, targetLanguage, vocabularyId } = validated.data;

      const settings = await getSettings();
      const db = meetingCorrectionService.ensureDatabase();
      const meeting = db.getMeeting(meetingId);
      if (!meeting) {
        return { success: false, error: 'Meeting not found' };
      }

      const isAgentic = meeting.type === 'agentic';
      const credentials = isAgentic ? null : await getCredentials();
      if (!isAgentic && !credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }

      const initialLanguage = language ?? settings.transcribe.languageCode;
      const initialTargetLanguage = targetLanguage ?? settings.transcribe.translationTargetLanguage ?? 'ko-KR';
      
      // 용어집 조회: 사용자 선택 > 기본 용어집 (agentic은 AWS 용어집을 건너뜀)
      let vocabularyName: string | undefined;
      if (!isAgentic) {
      try {
        const vocabularyService = getVocabularyService();

        if (vocabularyId) {
          // 사용자가 선택한 용어집 사용
          const selectedVocabulary = vocabularyService.getVocabulary(vocabularyId);
          if (!selectedVocabulary) {
            log.warn({ vocabularyId }, 'Selected vocabulary not found, proceeding without it');
          } else if (selectedVocabulary.languageCode !== initialLanguage) {
            log.warn(
              {
                vocabularyId,
                vocabularyLanguage: selectedVocabulary.languageCode,
                meetingLanguage: initialLanguage,
              },
              'Selected vocabulary language does not match meeting language, proceeding without it'
            );
          } else if (selectedVocabulary.awsVocabularyName && selectedVocabulary.awsStatus === 'READY') {
            vocabularyName = await vocabularyService.resolveUsableAwsVocabularyName(
              vocabularyId,
              initialLanguage
            ) ?? undefined;
            if (vocabularyName) {
              log.info({ vocabularyName, vocabularyId }, 'Using selected custom vocabulary');
            } else {
              log.warn(
                { vocabularyId },
                'Selected vocabulary failed AWS language/status validation, proceeding without it'
              );
            }
          } else {
            log.warn(
              {
                vocabularyId,
                awsStatus: selectedVocabulary.awsStatus,
              },
              'Selected vocabulary is not ready on AWS, proceeding without it'
            );
          }
        } else {
          // 기본 용어집 사용
          const defaultVocabulary = vocabularyService.getDefaultVocabulary(initialLanguage);
          if (
            defaultVocabulary &&
            defaultVocabulary.languageCode === initialLanguage &&
            defaultVocabulary.awsVocabularyName &&
            defaultVocabulary.awsStatus === 'READY'
          ) {
            vocabularyName = await vocabularyService.resolveUsableAwsVocabularyName(
              defaultVocabulary.id,
              initialLanguage
            ) ?? undefined;
            if (vocabularyName) {
              log.info({ vocabularyName, languageCode: initialLanguage }, 'Using default custom vocabulary');
            } else {
              log.warn(
                { defaultVocabularyId: defaultVocabulary.id, languageCode: initialLanguage },
                'Default vocabulary failed AWS language/status validation, proceeding without it'
              );
            }
          } else if (defaultVocabulary && defaultVocabulary.languageCode !== initialLanguage) {
            log.warn(
              {
                defaultVocabularyId: defaultVocabulary.id,
                vocabularyLanguage: defaultVocabulary.languageCode,
                meetingLanguage: initialLanguage,
              },
              'Default vocabulary language mismatch, proceeding without it'
            );
          }
        }
      } catch (error) {
        log.warn({ err: error }, 'Failed to get vocabulary, proceeding without it');
      }
      }

      // Initialize session
      sessionManager.createSession({
        meetingId,
        meetingType: meeting.type,
        language: initialLanguage,
        targetLanguage: initialTargetLanguage,
        sentenceBuffer: new SentenceBufferService(initialLanguage),
      });

      // vocabularyName과 translationTargetLanguage를 transcribeSettings에 추가
      const transcribeSettings = {
        ...settings.transcribe,
        vocabularyName,
        translationTargetLanguage: initialTargetLanguage,
      };

      await startStreaming(meetingId, meeting.type, { ...settings, transcribe: transcribeSettings }, credentials, language);
      db.updateMeetingStatus(meetingId, 'recording');

      return { success: true };
    } catch (error) {
      log.error({ err: error }, 'Failed to start meeting');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_PAUSE
  ipcMain.handle(IPC_CHANNELS.MEETING_PAUSE, async () => {
    try {
      const session = sessionManager.getSession();
      if (!session) {
        return { success: false, error: 'No active meeting' };
      }

      if (session.backend) {
        try {
          await session.backend.stopStreaming();
        } catch (err) {
          log.warn({ err: String(err) }, 'Pause drain degraded (일부 tail 유실 가능)');
          handleTranscriptionError(err instanceof Error ? err : new Error(String(err)));
        }
      }

      sessionManager.updateSession({
        transcribeTimeOffsetSec: session.lastSegmentEndTimeSec,
      });
      
      const meetingId = sessionManager.getMeetingId();
      if (meetingId) {
        meetingCorrectionService.ensureDatabase().updateMeetingStatus(meetingId, 'paused');
      }

      return { success: true };
    } catch (error) {
      log.error({ err: error }, 'Failed to pause meeting');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_RESUME
  ipcMain.handle(IPC_CHANNELS.MEETING_RESUME, async () => {
    try {
      const session = sessionManager.getSession();
      if (!session) {
        return { success: false, error: 'No active meeting' };
      }

      const isAgentic = session.meetingType === 'agentic';
      const credentials = isAgentic ? null : await getCredentials();
      if (!isAgentic && !credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }

      const settings = await getSettings();

      // 기본 용어집 조회 및 vocabularyName 설정 (agentic은 AWS 용어집을 건너뜀)
      let vocabularyName: string | undefined;
      if (!isAgentic) {
      try {
        const vocabularyService = getVocabularyService();
        const defaultVocabulary = vocabularyService.getDefaultVocabulary(session.language);
        if (
          defaultVocabulary &&
          defaultVocabulary.languageCode === session.language &&
          defaultVocabulary.awsVocabularyName &&
          defaultVocabulary.awsStatus === 'READY'
        ) {
          vocabularyName = await vocabularyService.resolveUsableAwsVocabularyName(
            defaultVocabulary.id,
            session.language
          ) ?? undefined;
          if (vocabularyName) {
            log.info({ vocabularyName, languageCode: session.language }, 'Resuming with custom vocabulary');
          } else {
            log.warn(
              { defaultVocabularyId: defaultVocabulary.id, languageCode: session.language },
              'Default vocabulary failed AWS language/status validation on resume, proceeding without it'
            );
          }
        } else if (defaultVocabulary && defaultVocabulary.languageCode !== session.language) {
          log.warn(
            {
              defaultVocabularyId: defaultVocabulary.id,
              vocabularyLanguage: defaultVocabulary.languageCode,
              meetingLanguage: session.language,
            },
            'Default vocabulary language mismatch on resume, proceeding without it'
          );
        }
      } catch (error) {
        log.warn({ err: error }, 'Failed to get default vocabulary for resume, proceeding without it');
      }
      }

      // session의 source/target 언어를 보존하며 transcribeSettings 구성
      const transcribeSettings = {
        ...settings.transcribe,
        vocabularyName, // agentic이면 undefined
        languageCode: session.language,
        translationTargetLanguage: session.targetLanguage,
      };
      
      await startStreaming(session.meetingId, session.meetingType, { ...settings, transcribe: transcribeSettings }, credentials, session.language);

      meetingCorrectionService.ensureDatabase().updateMeetingStatus(session.meetingId, 'recording');

      return { success: true };
    } catch (error) {
      log.error({ err: error }, 'Failed to resume meeting');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_STOP
  ipcMain.handle(IPC_CHANNELS.MEETING_STOP, async () => {
    // backend stop을 시도하기 전에는 스트림이 살아있다(=복구 가능). 시도하면 terminal.
    let streamStillActive = true;
    let stopDegraded = false;
    try {
      const session = sessionManager.getSession();

      if (session?.backend) {
        streamStillActive = false; // stop 시도 = 더 이상 복구 불가(terminal)
        try {
          await session.backend.stopStreaming();
        } catch (err) {
          stopDegraded = true;
          log.warn({ err: String(err) }, 'Stop drain degraded (일부 tail 유실 가능)');
          handleTranscriptionError(err instanceof Error ? err : new Error(String(err)));
        }
      }

      // Process remaining sentences
      if (session?.correctionService) {
        const remainingSentences = session.sentenceBuffer.flushAll();
        for (const sentence of remainingSentences) {
          await meetingCorrectionService.processCorrectedSentence(
            sentence,
            session.correctionService
          );
        }
      }

      if (session) {
        meetingCorrectionService.ensureDatabase().updateMeetingStatus(
          session.meetingId,
          'completed',
          new Date()
        );
      }

      return { success: true, streamStillActive, degraded: stopDegraded };
    } catch (error) {
      log.error({ err: error }, 'Failed to stop meeting');
      // backend stop을 이미 시도한(terminal) 상태에서 finalization이 실패했다면,
      // 히스토리가 'recording'에 머무르지 않도록 best-effort로 'completed' 마킹.
      // (catch는 finally보다 먼저 실행되므로 여기서는 아직 세션을 읽을 수 있다.)
      if (!streamStillActive) {
        try {
          const s = sessionManager.getSession();
          if (s) {
            meetingCorrectionService.ensureDatabase().updateMeetingStatus(s.meetingId, 'completed', new Date());
          }
        } catch (e2) {
          log.warn({ err: String(e2) }, 'best-effort terminal status update failed');
        }
      }
      return { success: false, error: String(error), streamStillActive, degraded: true };
    } finally {
      // 세션 상태는 stop 시도 여부와 무관하게 항상 정리해 main에 stranded 세션이 남지 않게 한다.
      // resetSession()은 세션을 null로 만드는 안전한 연산이라 항상 호출해도 무방하다.
      sessionManager.resetSession();
    }
  });

  // MEETING_GET
  ipcMain.handle(IPC_CHANNELS.MEETING_GET, async (_event, params: unknown) => {
    try {
      const validated = MeetingGetSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }
      const { id } = validated.data;
      const meeting = meetingCorrectionService.ensureDatabase().getMeeting(id);
      return { success: true, meeting };
    } catch (error) {
      log.error({ err: error }, 'Failed to get meeting');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_LIST
  ipcMain.handle(IPC_CHANNELS.MEETING_LIST, async (_event, params: unknown) => {
    try {
      const validated = MeetingListSchema.safeParse(params || {});
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }
      const { limit, offset } = validated.data;
      const meetings = meetingCorrectionService.ensureDatabase().listMeetings(limit, offset);
      return { success: true, meetings };
    } catch (error) {
      log.error({ err: error }, 'Failed to list meetings');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_DELETE
  ipcMain.handle(IPC_CHANNELS.MEETING_DELETE, async (_event, params: unknown) => {
    try {
      const validated = MeetingGetSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }
      const { id } = validated.data;
      meetingCorrectionService.ensureDatabase().deleteMeeting(id);
      return { success: true };
    } catch (error) {
      log.error({ err: error }, 'Failed to delete meeting');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_DELETE_ALL
  ipcMain.handle(IPC_CHANNELS.MEETING_DELETE_ALL, async () => {
    try {
      const deletedCount = meetingCorrectionService.ensureDatabase().deleteAllMeetings();
      return { success: true, deletedCount };
    } catch (error) {
      log.error({ err: error }, 'Failed to delete all meetings');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_UPDATE_METADATA
  ipcMain.handle(IPC_CHANNELS.MEETING_UPDATE_METADATA, async (_event, params: unknown) => {
    try {
      const validated = MeetingUpdateMetadataSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }
      const { id, metadata } = validated.data;
      const updated = meetingCorrectionService.ensureDatabase().updateMeetingMetadata(
        id,
        metadata as MeetingMetadata
      );
      if (!updated) {
        return { success: false, error: 'Meeting not found' };
      }
      return { success: true };
    } catch (error) {
      log.error({ err: error }, 'Failed to update meeting metadata');
      return { success: false, error: String(error) };
    }
  });

  // MEETING_GET_METADATA
  ipcMain.handle(IPC_CHANNELS.MEETING_GET_METADATA, async (_event, params: unknown) => {
    try {
      const validated = MeetingGetSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }
      const { id } = validated.data;
      const db = meetingCorrectionService.ensureDatabase();
      if (!db.meetingExists(id)) {
        return { success: false, error: 'Meeting not found' };
      }
      const metadata = db.getMeetingMetadata(id);
      return { success: true, metadata };
    } catch (error) {
      log.error({ err: error }, 'Failed to get meeting metadata');
      return { success: false, error: String(error) };
    }
  });

  // AUDIO_CHUNK
  ipcMain.on(IPC_CHANNELS.AUDIO_CHUNK, (_event, params: unknown) => {
    const session = sessionManager.getSession();
    if (session?.backend) {
      const validated = AudioChunkSchema.safeParse(params);
      if (validated.success) {
        const buffer = base64ToBuffer(validated.data.data);
        session.backend.addAudioChunk(buffer);
      } else {
        log.error('Invalid audio chunk data');
      }
    }
  });

  // MEETING_UPDATE_PREP_DATA
  ipcMain.handle(IPC_CHANNELS.MEETING_UPDATE_PREP_DATA, async (_event, params: unknown) => {
    try {
      const validated = MeetingUpdatePrepDataSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }
      const { prepData } = validated.data;
      if (!sessionManager.hasActiveSession()) {
        return { success: false, error: 'No active meeting session' };
      }
      sessionManager.setPrepData(prepData);
      return { success: true };
    } catch (error) {
      log.error({ err: error }, 'Failed to update prep data');
      return { success: false, error: String(error) };
    }
  });

  // ENGLISH_SUGGESTIONS
  ipcMain.handle(IPC_CHANNELS.ENGLISH_SUGGESTIONS, async (_event, params: unknown) => {
    try {
      const validated = EnglishSuggestionsSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }

      // Rate Limiting 체크 (ORCH-025)
      if (!rateLimiter.tryRequest(RATE_LIMIT_KEYS.ENGLISH_SUGGESTIONS)) {
        const retryAfter = Math.ceil(rateLimiter.getRetryAfterMs(RATE_LIMIT_KEYS.ENGLISH_SUGGESTIONS) / 1000);
        return { success: false, error: `요청이 너무 많습니다. ${retryAfter}초 후에 다시 시도해주세요.` };
      }

      const { meetingId, count } = validated.data;
      const credentials = await getCredentials();
      if (!credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }

      const settings = await getSettings();
      const db = meetingCorrectionService.ensureDatabase();
      const meeting = db.getMeeting(meetingId);
      if (!meeting && sessionManager.getMeetingId() !== meetingId) {
        return { success: false, error: 'Meeting not found' };
      }

      const context = meetingCorrectionService.getContextLinesForMeeting(meetingId);
      const translationService = meetingStreamingService.createBedrockService(
        credentials,
        settings.bedrock.translationModelId,
        settings.bedrock.maxTokens,
        settings.bedrock.temperature
      );

      const batchSize = Math.max(1, Math.min(count ?? 5, 10));
      const suggestions = await translationService.generateEnglishSuggestions(context, batchSize);

      return { success: true, suggestions };
    } catch (error) {
      log.error({ err: error }, 'Failed to generate English suggestions');
      return { success: false, error: String(error) };
    }
  });

  // ENGLISH_TRANSLATE
  ipcMain.handle(IPC_CHANNELS.ENGLISH_TRANSLATE, async (_event, params: unknown) => {
    try {
      const validated = EnglishTranslateSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }

      // Rate Limiting 체크 (ORCH-025)
      if (!rateLimiter.tryRequest(RATE_LIMIT_KEYS.TRANSLATION)) {
        const retryAfter = Math.ceil(rateLimiter.getRetryAfterMs(RATE_LIMIT_KEYS.TRANSLATION) / 1000);
        return { success: false, error: `요청이 너무 많습니다. ${retryAfter}초 후에 다시 시도해주세요.` };
      }

      const { meetingId, text } = validated.data;
      const credentials = await getCredentials();
      if (!credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }

      const trimmedText = text?.trim();
      if (!trimmedText) {
        return { success: false, error: '번역할 문장을 입력하세요.' };
      }

      if (meetingId) {
        const db = meetingCorrectionService.ensureDatabase();
        const meeting = db.getMeeting(meetingId);
        if (!meeting && sessionManager.getMeetingId() !== meetingId) {
          return { success: false, error: 'Meeting not found' };
        }
      }

      const settings = await getSettings();
      const context = meetingId ? meetingCorrectionService.getContextLinesForMeeting(meetingId) : [];
      const translationService = meetingStreamingService.createBedrockService(
        credentials,
        settings.bedrock.translationModelId,
        settings.bedrock.maxTokens,
        settings.bedrock.temperature
      );

      const translatedText = await translationService.translateToEnglish(trimmedText, context);

      return { success: true, translatedText };
    } catch (error) {
      log.error({ err: error }, 'Failed to translate text');
      return { success: false, error: String(error) };
    }
  });

  // INTERVIEW_SUGGESTIONS
  ipcMain.handle(IPC_CHANNELS.INTERVIEW_SUGGESTIONS, async (_event, params: unknown) => {
    try {
      const validated = InterviewSuggestionsSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }

      if (!rateLimiter.tryRequest(RATE_LIMIT_KEYS.ENGLISH_SUGGESTIONS)) {
        const retryAfter = Math.ceil(rateLimiter.getRetryAfterMs(RATE_LIMIT_KEYS.ENGLISH_SUGGESTIONS) / 1000);
        return { success: false, error: `요청이 너무 많습니다. ${retryAfter}초 후에 다시 시도해주세요.` };
      }

      const { meetingId, lpIds, count } = validated.data;
      const credentials = await getCredentials();
      if (!credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }

      const settings = await getSettings();
      const db = meetingCorrectionService.ensureDatabase();
      const meeting = db.getMeeting(meetingId);
      if (!meeting && sessionManager.getMeetingId() !== meetingId) {
        return { success: false, error: 'Meeting not found' };
      }

      const context = meetingCorrectionService.getContextLinesForMeeting(meetingId);
      const bedrockService = meetingStreamingService.createBedrockService(
        credentials,
        settings.bedrock.translationModelId,
        settings.bedrock.maxTokens,
        settings.bedrock.temperature
      );

      const batchSize = Math.max(1, Math.min(count ?? 5, 10));
      const suggestions = await bedrockService.generateInterviewSuggestions(
        context,
        lpIds as import('../../shared/types/interview').LeadershipPrinciple[],
        batchSize
      );

      return { success: true, suggestions };
    } catch (error) {
      log.error({ err: error }, 'Failed to generate interview suggestions');
      return { success: false, error: String(error) };
    }
  });

  // SUMMARY_GENERATE
  ipcMain.handle(IPC_CHANNELS.SUMMARY_GENERATE, async (_event, params: unknown) => {
    try {
      const validated = SummaryGenerateSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }

      // Rate Limiting 체크 (ORCH-025)
      if (!rateLimiter.tryRequest(RATE_LIMIT_KEYS.SUMMARY_GENERATION)) {
        const retryAfter = Math.ceil(rateLimiter.getRetryAfterMs(RATE_LIMIT_KEYS.SUMMARY_GENERATION) / 1000);
        return { success: false, error: `요약 생성 요청이 너무 많습니다. ${retryAfter}초 후에 다시 시도해주세요.` };
      }

      const { meetingId, prepData } = validated.data;
      const credentials = await getCredentials();
      if (!credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }

      const settings = await getSettings();
      const db = meetingCorrectionService.ensureDatabase();
      const meeting = db.getMeeting(meetingId);

      if (!meeting) {
        return { success: false, error: 'Meeting not found' };
      }

      const summaryService = meetingStreamingService.createBedrockService(
        credentials,
        settings.bedrock.summaryModelId,
        15000, // High token limit for comprehensive summary
        settings.bedrock.temperature
      );

      // Build transcript with prep data context
      let transcriptText = '';
      if (prepData && isMeetingPrepDataValid(prepData)) {
        transcriptText += formatMeetingPrepAsSegment(prepData) + '\n\n';
      }

      transcriptText += meeting.correctedSentences.length > 0
        ? meeting.correctedSentences.map((s) => `[${s.speakerLabel || 'Speaker'}] ${s.correctedText}`).join('\n')
        : meeting.segments.map((s) => `[${s.speakerLabel || 'Speaker'}] ${s.text}`).join('\n');

      const summaryResult = await summaryService.generateSummary(transcriptText, meeting.language);

      const summary = {
        id: uuidv4(),
        meetingId,
        mainTopics: summaryResult.mainTopics,
        topicDiscussions: summaryResult.topicDiscussions,
        keyTakeaways: summaryResult.keyTakeaways,
        confirmedActions: summaryResult.confirmedActions,
        pendingActions: summaryResult.pendingActions,
        followUps: summaryResult.followUps,
        openIssues: summaryResult.openIssues,
        modelId: settings.bedrock.summaryModelId,
      };

      db.saveSummary(summary);
      sendToRenderer(IPC_CHANNELS.SUMMARY_COMPLETE, { meetingId, summary });

      return { success: true, summary };
    } catch (error) {
      log.error({ err: error }, 'Failed to generate summary');
      return { success: false, error: String(error) };
    }
  });

  // CONVERSATION_LOG_GENERATE
  ipcMain.handle(IPC_CHANNELS.CONVERSATION_LOG_GENERATE, async (_event, params: unknown) => {
    try {
      const validated = ConversationLogGenerateSchema.safeParse(params);
      if (!validated.success) {
        return { success: false, error: `Invalid parameters: ${validated.error.message}` };
      }

      // Rate Limiting 체크
      if (!rateLimiter.tryRequest(RATE_LIMIT_KEYS.CONVERSATION_LOG_GENERATION)) {
        const retryAfter = Math.ceil(rateLimiter.getRetryAfterMs(RATE_LIMIT_KEYS.CONVERSATION_LOG_GENERATION) / 1000);
        return { success: false, error: `대화 요약 생성 요청이 너무 많습니다. ${retryAfter}초 후에 다시 시도해주세요.` };
      }

      const { meetingId } = validated.data;
      const credentials = await getCredentials();
      if (!credentials) {
        return { success: false, error: 'AWS credentials not configured' };
      }

      const settings = await getSettings();
      const db = meetingCorrectionService.ensureDatabase();
      const meeting = db.getMeeting(meetingId);

      if (!meeting) {
        return { success: false, error: 'Meeting not found' };
      }

      const conversationLogService = meetingStreamingService.createBedrockService(
        credentials,
        settings.bedrock.summaryModelId,
        10000, // High token limit for conversation log
        settings.bedrock.temperature
      );

      // Build transcript: correctedSentences 우선, 없으면 segments 사용
      const transcriptText = meeting.correctedSentences.length > 0
        ? meeting.correctedSentences.map((s) => `[${s.speakerLabel || 'Speaker'}] ${s.correctedText}`).join('\n')
        : meeting.segments.map((s) => `[${s.speakerLabel || 'Speaker'}] ${s.text}`).join('\n');

      const topics = await conversationLogService.generateConversationLog(transcriptText, meeting.language);

      const conversationLog = {
        id: uuidv4(),
        meetingId,
        topics,
        modelId: settings.bedrock.summaryModelId,
      };

      db.saveConversationLog(conversationLog);
      sendToRenderer(IPC_CHANNELS.CONVERSATION_LOG_COMPLETE, { meetingId, conversationLog });

      return { success: true, conversationLog };
    } catch (error) {
      log.error({ err: error }, 'Failed to generate conversation log');
      return { success: false, error: String(error) };
    }
  });
}

// ============================================================================
// Cleanup
// ============================================================================

export function closeDatabaseConnection(): void {
  meetingCorrectionService.closeDatabase();
}
