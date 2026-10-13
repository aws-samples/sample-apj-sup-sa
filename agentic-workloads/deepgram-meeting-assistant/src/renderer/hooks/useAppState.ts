import { useState, useCallback, useEffect } from 'react';
import type { Meeting, MeetingDetail, MeetingType, NavItem, RecordingStatus } from '@shared/types/meeting';
import type { TranscribeLanguage } from '@shared/types/settings';
import { useMeeting } from './useMeeting';
import { useMeetingHistory } from './useMeetingHistory';

export const useAppState = () => {
  const [activeNav, setActiveNav] = useState<NavItem>('home');
  const [selectedMeetingType, setSelectedMeetingType] = useState<MeetingType | null>(null);
  const [selectedMeetingId, setSelectedMeetingId] = useState<string | null>(null);
  const [selectedMeetingDetail, setSelectedMeetingDetail] = useState<MeetingDetail | null>(null);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);
  const [isMicMuted, setIsMicMuted] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [appError, setAppError] = useState<string | null>(null);

  const {
    meetings,
    isLoading: isHistoryLoading,
    refresh: refreshMeetingHistory,
    hasMore,
    loadMore,
  } = useMeetingHistory();

  const {
    recordingState,
    error: meetingError,
    vocabularies,
    createAndStartMeeting,
    pauseMeeting,
    resumeMeeting,
    stopMeeting,
    resetMeeting,
    setRecordingState,
    setLanguage,
    setTargetLanguage,
    setVocabularyId,
    clearError: clearMeetingError,
  } = useMeeting();

  const clearError = useCallback(() => {
    setHistoryError(null);
    setAppError(null);
    clearMeetingError();
  }, [clearMeetingError]);

  const handleMeetingTypeSelect = (type: MeetingType) => {
    setHistoryError(null);
    setAppError(null);
    setSelectedMeetingId(null);
    setSelectedMeetingDetail(null);
    setSelectedMeetingType(type);
    setRecordingState((prev) => ({
      ...prev,
      meetingType: type,
    }));

    if (type === 'english') {
      setLanguage('en-US');
    }
  };

  const handleBackToSelection = () => {
    setHistoryError(null);
    setAppError(null);
    setSelectedMeetingType(null);
    setSelectedMeetingId(null);
    setSelectedMeetingDetail(null);
    resetMeeting();
  };

  const handleStartRecording = useCallback(async () => {
    if (!selectedMeetingType) return;
    setHistoryError(null);
    setAppError(null);
    setSelectedMeetingId(null);
    setSelectedMeetingDetail(null);
    const language: TranscribeLanguage = recordingState.language;
    const targetLanguage: TranscribeLanguage = recordingState.targetLanguage;
    await createAndStartMeeting(selectedMeetingType, language, undefined, undefined, targetLanguage);
  }, [selectedMeetingType, recordingState.language, recordingState.targetLanguage, createAndStartMeeting]);

  const handlePauseRecording = useCallback(async () => {
    await pauseMeeting();
  }, [pauseMeeting]);

  const handleResumeRecording = useCallback(async () => {
    await resumeMeeting();
  }, [resumeMeeting]);

  const handleEndRecording = useCallback(async () => {
    return await stopMeeting();
  }, [stopMeeting]);

  const handleDeviceChange = useCallback((deviceId: string | null) => {
    setSelectedDeviceId(deviceId);
  }, []);

  const handleLanguageChange = useCallback((language: TranscribeLanguage) => {
    setLanguage(language);
  }, [setLanguage]);

  const handleTargetLanguageChange = useCallback((language: TranscribeLanguage) => {
    setTargetLanguage(language);
  }, [setTargetLanguage]);

  const mapMeetingStatus = useCallback((status: MeetingDetail['status']): RecordingStatus => {
    switch (status) {
      case 'recording':
        return 'recording';
      case 'paused':
        return 'paused';
      case 'completed':
      case 'cancelled':
      default:
        return 'completed';
    }
  }, []);

  const handleMeetingSelect = useCallback(async (meeting: Meeting) => {
    if (!window.electronAPI) return;

    const isBusy = recordingState.status === 'recording'
      || recordingState.status === 'processing';
    const isLiveSession = isBusy && !selectedMeetingDetail;

    if (isLiveSession && meeting.id !== recordingState.meetingId) {
      setHistoryError('녹음 중에는 히스토리를 열 수 없습니다.');
      setTimeout(() => {
        setHistoryError(null);
      }, 3000);
      return;
    }

    const result = await window.electronAPI.getMeeting({ id: meeting.id });
    if (!result.success || !result.meeting) {
      setHistoryError(result.error || '미팅 정보를 불러오지 못했습니다.');
      return;
    }

    const detail = result.meeting;
    setHistoryError(null);
    setAppError(null);
    setActiveNav('home');
    setSelectedMeetingId(detail.id);
    setSelectedMeetingDetail(detail);
    setSelectedMeetingType(detail.type);
    setRecordingState({
      status: mapMeetingStatus(detail.status),
      meetingId: detail.id,
      meetingType: detail.type,
      language: detail.language,
      targetLanguage: recordingState.targetLanguage,  // Preserve current target language
      vocabularyId: detail.vocabularyId ?? null,
      startTime: detail.startedAt ? new Date(detail.startedAt) : null,
      duration: detail.duration,
    });
  }, [mapMeetingStatus, recordingState.meetingId, recordingState.status, recordingState.targetLanguage, selectedMeetingDetail, setRecordingState]);

  const handleMeetingDelete = useCallback(async (meetingId: string) => {
    if (!window.electronAPI) return;

    const meeting = meetings.find((m) => m.id === meetingId);
    if (!meeting) return;

    const confirmed = confirm(`"${meeting.title}" 미팅을 삭제하시겠습니까?\n\n이 작업은 되돌릴 수 없습니다.`);
    if (!confirmed) return;

    const result = await window.electronAPI.deleteMeeting({ id: meetingId });
    if (!result.success) {
      setHistoryError(result.error || '미팅 삭제에 실패했습니다.');
      return;
    }

    if (selectedMeetingId === meetingId) {
      setSelectedMeetingId(null);
      setSelectedMeetingDetail(null);
      setSelectedMeetingType(null);
      resetMeeting();
    }

    refreshMeetingHistory();
    setHistoryError(null);
  }, [meetings, selectedMeetingId, resetMeeting, refreshMeetingHistory]);

  useEffect(() => {
    if (recordingState.meetingId) {
      refreshMeetingHistory();
    }
  }, [recordingState.meetingId, refreshMeetingHistory]);

  useEffect(() => {
    if (recordingState.status === 'completed') {
      refreshMeetingHistory();
    }
  }, [recordingState.status, refreshMeetingHistory]);

  useEffect(() => {
    const isBusy = recordingState.status === 'recording'
      || recordingState.status === 'processing';
    const isLiveSession = isBusy && !selectedMeetingDetail;
    if (!isLiveSession && historyError) {
      setHistoryError(null);
    }
  }, [recordingState.status, historyError, selectedMeetingDetail]);

  useEffect(() => {
    if (!window.electronAPI) return;
    const unsubscribe = window.electronAPI.onMeetingTitleUpdated(() => {
      refreshMeetingHistory();
    });
    return unsubscribe;
  }, [refreshMeetingHistory]);

  const toggleMute = useCallback(() => setIsMicMuted((prev) => !prev), []);

  return {
    // State
    activeNav,
    selectedMeetingType,
    selectedMeetingId,
    selectedMeetingDetail,
    selectedDeviceId,
    isMicMuted,
    historyError,
    meetingError,
    appError,
    meetings,
    isHistoryLoading,
    hasMore,
    recordingState,
    vocabularies,

    // Setters / Handlers
    setActiveNav,
    loadMore,
    handleMeetingTypeSelect,
    handleBackToSelection,
    handleMeetingSelect,
    handleMeetingDelete,
    handleStartRecording,
    handlePauseRecording,
    handleResumeRecording,
    handleEndRecording,
    handleDeviceChange,
    handleLanguageChange,
    handleTargetLanguageChange,
    handleVocabularyChange: setVocabularyId,
    toggleMute,
    setRecordingState,
    setAppError,
    clearError,
  };
};
