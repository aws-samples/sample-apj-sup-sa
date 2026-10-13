import { useCallback, useEffect, useState } from 'react';
import type { MeetingSummary, MeetingPrepData } from '@shared/types';

interface SummaryState {
  summary: MeetingSummary | null;
  isLoading: boolean;
  error: string | null;
}

export function useSummary(currentMeetingId?: string | null) {
  const [state, setState] = useState<SummaryState>({
    summary: null,
    isLoading: false,
    error: null,
  });

  useEffect(() => {
    setState({
      summary: null,
      isLoading: false,
      error: null,
    });
  }, [currentMeetingId]);

  useEffect(() => {
    if (!window.electronAPI) {
      return;
    }

    const unsubscribe = window.electronAPI.onSummaryComplete((data) => {
      if (currentMeetingId && data.meetingId !== currentMeetingId) {
        return;
      }
      setState({ summary: data.summary, isLoading: false, error: null });
    });

    return () => {
      unsubscribe();
    };
  }, [currentMeetingId]);

  const requestSummary = useCallback(async (meetingId: string | null, prepData?: MeetingPrepData | null) => {
    if (!meetingId) {
      setState((prev) => ({ ...prev, error: '미팅 ID가 없습니다.' }));
      return null;
    }

    if (!window.electronAPI) {
      setState((prev) => ({
        ...prev,
        error: '요약 기능은 데스크톱 앱에서만 사용할 수 있습니다.',
      }));
      return null;
    }

    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    const result = await window.electronAPI.generateSummary({ meetingId, prepData });

    if (!result.success || !result.summary) {
      setState((prev) => ({
        ...prev,
        isLoading: false,
        error: result.error || '요약 생성 실패',
      }));
      return null;
    }

    setState({ summary: result.summary, isLoading: false, error: null });
    return result.summary;
  }, []);

  const clearSummary = useCallback(() => {
    setState({ summary: null, isLoading: false, error: null });
  }, []);

  return {
    ...state,
    requestSummary,
    clearSummary,
  };
}
