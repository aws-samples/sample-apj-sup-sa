import { useState, useCallback, useEffect } from 'react';
import type {
  MeetingType,
  TranscribeLanguage,
  RecordingState,
} from '@shared/types';
import type { Vocabulary } from '@shared/types/vocabulary';

const initialRecordingState: RecordingState = {
  status: 'idle',
  meetingId: null,
  meetingType: null,
  language: 'ko-KR',
  targetLanguage: 'ko-KR',
  vocabularyId: null,
  startTime: null,
  duration: 0,
};

export function useMeeting() {
  const [recordingState, setRecordingState] = useState<RecordingState>(initialRecordingState);
  const [error, setError] = useState<string | null>(null);
  const [vocabularies, setVocabularies] = useState<Vocabulary[]>([]);

  // 용어집 목록 로드 (초기 1회)
  useEffect(() => {
    const loadVocabularies = async () => {
      if (!window.electronAPI?.vocabulary) return;
      try {
        const list = await window.electronAPI.vocabulary.list();
        setVocabularies(list);
        
        // 현재 언어(ko-KR)의 기본 용어집 설정
        const defaultVocab = list.find(
          (v) => v.languageCode === 'ko-KR' && v.isDefault && v.awsStatus === 'READY'
        );
        if (defaultVocab) {
          setRecordingState((prev) => ({ ...prev, vocabularyId: defaultVocab.id }));
        }
      } catch (err) {
        console.error('Failed to load vocabularies:', err);
      }
    };
    loadVocabularies();
  }, []);

  // 언어 변경 시 해당 언어의 기본 용어집으로 전환
  const setLanguage = useCallback((language: TranscribeLanguage) => {
    setRecordingState((prev) => {
      const defaultVocab = vocabularies.find(
        (v) => v.languageCode === language && v.isDefault && v.awsStatus === 'READY'
      );
      return {
        ...prev,
        language,
        vocabularyId: defaultVocab?.id ?? null,
      };
    });
  }, [vocabularies]);

  const setVocabularyId = useCallback((vocabularyId: string | null) => {
    setRecordingState((prev) => ({ ...prev, vocabularyId }));
  }, []);

  const setTargetLanguage = useCallback((targetLanguage: TranscribeLanguage) => {
    setRecordingState((prev) => ({ ...prev, targetLanguage }));
  }, []);

  const createAndStartMeeting = useCallback(
    async (type: MeetingType, language: TranscribeLanguage, title?: string, vocabularyId?: string | null, targetLanguage?: TranscribeLanguage) => {
      setError(null);

      const createResult = await window.electronAPI.createMeeting({ type, language, title });
      if (!createResult.success || !createResult.meeting) {
        setError(createResult.error || '미팅 생성 실패');
        return null;
      }

      const meeting = createResult.meeting;
      const finalTargetLanguage = targetLanguage ?? recordingState.targetLanguage;

      setRecordingState((prev) => ({
        status: 'recording',
        meetingId: meeting.id,
        meetingType: type,
        language,
        targetLanguage: finalTargetLanguage,
        vocabularyId: vocabularyId ?? prev.vocabularyId,
        startTime: new Date(),
        duration: 0,
      }));

      const startResult = await window.electronAPI.startMeeting({
        meetingId: meeting.id,
        language,
        targetLanguage: finalTargetLanguage,
        vocabularyId: vocabularyId ?? recordingState.vocabularyId ?? undefined,
      });
      if (!startResult.success) {
        setError(startResult.error || '녹음 시작 실패');
        setRecordingState(initialRecordingState);
        return null;
      }

      return meeting;
    },
    [recordingState.vocabularyId, recordingState.targetLanguage]
  );

  const pauseMeeting = useCallback(async () => {
    setRecordingState((prev) => ({ ...prev, status: 'processing' }));

    const result = await window.electronAPI.pauseMeeting();
    if (!result.success) {
      setError(result.error || '녹음 일시 정지 실패');
      setRecordingState((prev) => ({ ...prev, status: 'recording' }));
      return false;
    }

    setRecordingState((prev) => ({ ...prev, status: 'paused' }));
    return true;
  }, []);

  const resumeMeeting = useCallback(async () => {
    setRecordingState((prev) => ({ ...prev, status: 'processing' }));

    const result = await window.electronAPI.resumeMeeting();
    if (!result.success) {
      setError(result.error || '녹음 재개 실패');
      setRecordingState((prev) => ({ ...prev, status: 'paused' }));
      return false;
    }

    setRecordingState((prev) => ({ ...prev, status: 'recording' }));
    return true;
  }, []);

  const stopMeeting = useCallback(async (): Promise<{ completed: boolean; degraded: boolean; recoverable: boolean }> => {
    setRecordingState((prev) => ({ ...prev, status: 'processing' }));

    const result = await window.electronAPI.stopMeeting();
    // 스트림이 아직 살아있을 때만 복구 가능. (backend stop을 시도조차 안 한 경우)
    const recoverable = result.streamStillActive === true;

    if (!result.success) {
      setError(result.error || '녹음 종료 실패');
      if (recoverable) {
        setRecordingState((prev) => ({ ...prev, status: 'recording' }));
        return { completed: false, degraded: false, recoverable: true };
      }
      setRecordingState((prev) => ({ ...prev, status: 'completed' }));
      return { completed: true, degraded: true, recoverable: false };
    }

    setRecordingState((prev) => ({ ...prev, status: 'completed' }));
    if (result.degraded) {
      setError('일부 전사/교정이 저장되지 않았을 수 있습니다. 요약은 자동 생성되지 않습니다.');
    }
    return { completed: true, degraded: Boolean(result.degraded), recoverable: false };
  }, []);

  const resetMeeting = useCallback(() => {
    setRecordingState(initialRecordingState);
    setError(null);
  }, []);

  const updateDuration = useCallback((duration: number) => {
    setRecordingState((prev) => ({ ...prev, duration }));
  }, []);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  return {
    recordingState,
    error,
    vocabularies,
    createAndStartMeeting,
    pauseMeeting,
    resumeMeeting,
    stopMeeting,
    resetMeeting,
    updateDuration,
    setRecordingState,
    setLanguage,
    setTargetLanguage,
    setVocabularyId,
    clearError,
  };
}
