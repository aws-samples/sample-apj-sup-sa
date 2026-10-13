import { useEffect, useRef, useCallback, useMemo, useState } from 'react';
import {
  MEETING_TYPES,
  type MeetingDetail,
  type MeetingType,
  type RecordingState,
  type AudioChunk,
  type TranscribeLanguage,
  type MeetingPrepData,
} from '@shared/types';
import type { Vocabulary } from '@shared/types/vocabulary';
import { useAudioCapture } from '../hooks/useAudioCapture';
import useRecordingControls from '../hooks/useRecordingControls';
import { useSummary } from '../hooks/useSummary';
import { useConversationLog } from '../hooks/useConversationLog';
import { useTranscription } from '../hooks/useTranscription';
import { buildFullScript } from '../utils/transcript-format';
import { copyMeetingContent } from '../utils/clipboard';
import {
  QuickMeetingView,
  ClientMeetingView,
  InterviewMeetingView,
  TranslatedMeetingView,
  AgenticMeetingView,
} from './meeting-types';
import type { QuickMeetingTab } from './meeting-types/types';

interface MeetingViewProps {
  meetingType: MeetingType;
  recordingState: RecordingState;
  setRecordingState: React.Dispatch<React.SetStateAction<RecordingState>>;
  onBack: () => void;
  selectedDeviceId: string | null;
  isMicMuted: boolean;
  onDeviceChange: (deviceId: string | null) => void;
  onToggleMute: () => void;
  onStartRecording: () => Promise<void>;
  onPauseRecording: () => Promise<void>;
  onResumeRecording: () => Promise<void>;
  onEndRecording: () => Promise<{ completed: boolean; degraded: boolean; recoverable: boolean }>;
  onLanguageChange?: (language: TranscribeLanguage) => void;
  onTargetLanguageChange?: (language: TranscribeLanguage) => void;
  vocabularies?: Vocabulary[];
  onVocabularyChange?: (vocabularyId: string | null) => void;
  meetingDetail?: MeetingDetail | null;
  onError?: (message: string | null) => void;
}

function MeetingView({
  meetingType,
  recordingState,
  setRecordingState,
  onBack,
  selectedDeviceId,
  isMicMuted,
  onDeviceChange,
  onToggleMute,
  onStartRecording,
  onPauseRecording,
  onResumeRecording,
  onEndRecording,
  onLanguageChange,
  onTargetLanguageChange,
  vocabularies = [],
  onVocabularyChange,
  meetingDetail,
  onError,
}: MeetingViewProps) {
  const {
    partialText,
    partialSpeaker,
    segments,
    correctedSentences,
    error: transcriptionError,
    clearTranscription,
  } = useTranscription();

  const recordingStatusRef = useRef(recordingState.status);

  useEffect(() => {
    recordingStatusRef.current = recordingState.status;
  }, [recordingState.status]);

  const handleAudioChunk = useCallback((chunk: AudioChunk) => {
    if (recordingStatusRef.current !== 'recording') {
      return;
    }
    if (!window.electronAPI) {
      return;
    }
    window.electronAPI.sendAudioChunk({ data: chunk.data });
  }, []);

  const isViewingHistory = Boolean(meetingDetail && meetingDetail.id === recordingState.meetingId);
  const { state: audioState, startCapture, stopCapture, setMuted } = useAudioCapture(handleAudioChunk);
  const targetMeetingId = isViewingHistory ? meetingDetail?.id : recordingState.meetingId;
  const { summary, isLoading: isSummaryLoading, error: summaryError, requestSummary, clearSummary } = useSummary(targetMeetingId);
  const {
    conversationLog,
    isLoading: isConversationLogLoading,
    error: conversationLogError,
    requestConversationLog,
    clearConversationLog,
    setConversationLog,
  } = useConversationLog(targetMeetingId);
  const [activeTab, setActiveTab] = useState<QuickMeetingTab>('script');
  const [isReverseScript, setIsReverseScript] = useState(false);
  const [meetingTitle, setMeetingTitle] = useState('Untitled');
  const [prepData, setPrepData] = useState<MeetingPrepData | null>(null);
  const prepDataRef = useRef<MeetingPrepData | null>(null);

  useEffect(() => {
    if (!selectedDeviceId) return;
    const isActive = recordingState.status === 'recording' || recordingState.status === 'paused';
    if (!isActive || !audioState.isCapturing) return;
    if (!audioState.deviceId || audioState.deviceId === selectedDeviceId) return;

    let cancelled = false;
    const restartCapture = async () => {
      stopCapture();
      if (!cancelled) {
        await startCapture(selectedDeviceId);
      }
    };
    restartCapture();

    return () => {
      cancelled = true;
    };
  }, [audioState.deviceId, audioState.isCapturing, recordingState.status, selectedDeviceId, startCapture, stopCapture]);

  // prepData 변경 시 ref 업데이트 및 main process에 전달
  useEffect(() => {
    prepDataRef.current = prepData;
    // 녹음 중일 때만 main process에 prepData 업데이트
    if (recordingState.status === 'recording' || recordingState.status === 'paused') {
      window.electronAPI?.updatePrepData({ prepData });
    }
  }, [prepData, recordingState.status]);

  const displaySegments = isViewingHistory ? meetingDetail?.segments ?? [] : segments;
  const displayCorrectedSentences = isViewingHistory ? meetingDetail?.correctedSentences ?? [] : correctedSentences;
  // 히스토리 조회 중에도 재생성/수정된 요약이 있으면 라이브 상태(useSummary)를 우선.
  // (displayConversationLog와 동일 패턴 — 회의록 어시스턴트가 SUMMARY_COMPLETE를
  // 재emit하면 meetingDetail 스냅샷이 stale이어도 화면이 최신 DB를 반영한다.)
  const displaySummary = isViewingHistory
    ? (summary ?? meetingDetail?.summary ?? null)
    : summary;
  const displaySummaryLoading = isViewingHistory ? false : isSummaryLoading;
  const displayConversationLogLoading = isViewingHistory ? false : isConversationLogLoading;
  // 히스토리 조회 중에도 재생성된 대화 로그가 있으면 로컬 상태 사용
  const displayConversationLog = isViewingHistory
    ? (conversationLog ?? meetingDetail?.conversationLog ?? null)
    : conversationLog;
  const displayPartialText = isViewingHistory ? '' : partialText;
  const displayPartialSpeaker = isViewingHistory ? null : partialSpeaker;
  const fullScript = useMemo(
    () => buildFullScript(displaySegments, displayCorrectedSentences),
    [displaySegments, displayCorrectedSentences]
  );

  const combinedError = transcriptionError
    || audioState.error
    || summaryError
    || conversationLogError
    || null;

  useEffect(() => {
    if (onError) {
      onError(combinedError);
    }
  }, [combinedError, onError]);

  useEffect(() => {
    if (recordingState.status === 'idle') {
      setMeetingTitle('Untitled');
      setPrepData(null);
    }
  }, [recordingState.status]);

  useEffect(() => {
    if (isViewingHistory && meetingDetail) {
      setMeetingTitle(meetingDetail.title || 'Untitled');
      // 히스토리 조회 시 대화 로그 상태 설정
      if (meetingDetail.conversationLog) {
        setConversationLog(meetingDetail.conversationLog);
      }
    }
  }, [isViewingHistory, meetingDetail, setConversationLog]);

  useEffect(() => {
    if (!window.electronAPI) return;
    
    const unsubscribe = window.electronAPI.onMeetingTitleUpdated(({ meetingId, title }) => {
      if (meetingId === recordingState.meetingId) {
        setMeetingTitle(title);
      }
    });

    return unsubscribe;
  }, [recordingState.meetingId]);

  const handleStartSetup = useCallback(() => {
    clearTranscription();
    clearSummary();
    clearConversationLog();
    setActiveTab('script');
  }, [clearTranscription, clearSummary, clearConversationLog]);

  const handleRecordingComplete = useCallback(() => {
    if (recordingState.meetingId) {
      setActiveTab('summary');
      // prepData를 summary 생성 시 전달 (Requirements: 8.5)
      requestSummary(recordingState.meetingId, prepDataRef.current);
      // 대화 로그 자동 생성
      requestConversationLog(recordingState.meetingId);
    }
  }, [recordingState.meetingId, requestSummary, requestConversationLog]);

  const { handleStart, handlePause, handleResume, handleEnd } = useRecordingControls({
    selectedDeviceId,
    isCapturing: audioState.isCapturing,
    startCapture,
    stopCapture,
    onStartRecording,
    onPauseRecording,
    onResumeRecording,
    onEndRecording,
    setRecordingState,
    onStartSetup: handleStartSetup,
    onStatusChange: (status) => {
      recordingStatusRef.current = status;
    },
    onRecordingComplete: handleRecordingComplete,
  });

  const meetingLabel = useMemo(() => {
    const config = MEETING_TYPES.find((type) => type.id === meetingType);
    return config?.label ?? 'Meeting';
  }, [meetingType]);
  const displayLanguage = useMemo<TranscribeLanguage>(() => {
    return (meetingType === 'english' || meetingType === 'translated') ? 'ko-KR' : recordingState.language;
  }, [meetingType, recordingState.language]);

  const handleCopy = useCallback(() => {
    copyMeetingContent(activeTab, displaySegments, displayCorrectedSentences, fullScript, displaySummary, displayConversationLog);
  }, [activeTab, displayCorrectedSentences, displaySegments, displaySummary, displayConversationLog, fullScript]);

  useEffect(() => {
    setMuted(isMicMuted);
  }, [isMicMuted, setMuted]);

  // 공통 props 정의
  const commonProps = {
    recordingState,
    segments: displaySegments,
    correctedSentences: displayCorrectedSentences,
    partialText: displayPartialText,
    partialSpeaker: displayPartialSpeaker,
    summary: displaySummary,
    fullScript,
    transcriptionError,
    audioError: audioState.error,
    summaryError,
    isSummaryLoading: displaySummaryLoading,
  };

  const meetingWorkspaceProps = {
    ...commonProps,
    activeTab,
    onTabChange: setActiveTab,
    onCopyNotes: handleCopy,
    isReverseScript,
    onToggleReverseScript: () => setIsReverseScript((prev) => !prev),
    onStart: handleStart,
    onPause: handlePause,
    onResume: handleResume,
    onStop: handleEnd,
    selectedDeviceId,
    isMicMuted,
    onToggleMute,
    onDeviceChange,
    meetingTitle,
    onTitleChange: setMeetingTitle,
    onLanguageChange,
    onTargetLanguageChange,
    displayLanguage,
    meetingLabel,
    onBack,
    prepData,
    onPrepDataChange: setPrepData,
    isViewingHistory,
    onRequestSummary: requestSummary,
    conversationLog: displayConversationLog,
    isConversationLogLoading: displayConversationLogLoading,
    conversationLogError,
    onRequestConversationLog: requestConversationLog,
    onError,
    vocabularies,
    onVocabularyChange,
  };

  // Meeting type별 컴포넌트 렌더링
  switch (meetingType) {
    case 'weekly':
      return (
        <QuickMeetingView {...meetingWorkspaceProps} />
      );
    case 'client':
      return <ClientMeetingView {...meetingWorkspaceProps} />;
    case 'interview':
      return <InterviewMeetingView {...meetingWorkspaceProps} />;
    case 'english':
    case 'translated':
      return <TranslatedMeetingView {...meetingWorkspaceProps} />;
    case 'agentic':
      return <AgenticMeetingView {...meetingWorkspaceProps} />;
    default:
      return <div>지원하지 않는 미팅 타입입니다.</div>;
  }
}

export default MeetingView;
