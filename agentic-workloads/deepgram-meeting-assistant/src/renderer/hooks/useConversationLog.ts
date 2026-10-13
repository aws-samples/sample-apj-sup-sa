import { useCallback, useEffect, useState } from 'react';
import type { ConversationLog } from '@shared/types';

interface ConversationLogState {
  conversationLog: ConversationLog | null;
  isLoading: boolean;
  error: string | null;
}

export function useConversationLog(currentMeetingId?: string | null) {
  const [state, setState] = useState<ConversationLogState>({
    conversationLog: null,
    isLoading: false,
    error: null,
  });

  useEffect(() => {
    setState({
      conversationLog: null,
      isLoading: false,
      error: null,
    });
  }, [currentMeetingId]);

  useEffect(() => {
    if (!window.electronAPI) {
      return;
    }

    const unsubscribe = window.electronAPI.onConversationLogComplete((data) => {
      if (currentMeetingId && data.meetingId !== currentMeetingId) {
        return;
      }
      setState({ conversationLog: data.conversationLog, isLoading: false, error: null });
    });

    return () => {
      unsubscribe();
    };
  }, [currentMeetingId]);

  const requestConversationLog = useCallback(async (meetingId: string | null) => {
    if (!meetingId) {
      setState((prev) => ({ ...prev, error: '미팅 ID가 없습니다.' }));
      return null;
    }

    if (!window.electronAPI) {
      setState((prev) => ({
        ...prev,
        error: '대화 요약 기능은 데스크톱 앱에서만 사용할 수 있습니다.',
      }));
      return null;
    }

    setState((prev) => ({ ...prev, isLoading: true, error: null }));
    const result = await window.electronAPI.generateConversationLog({ meetingId });

    if (!result.success || !result.conversationLog) {
      setState((prev) => ({
        ...prev,
        isLoading: false,
        error: result.error || '대화 요약 생성 실패',
      }));
      return null;
    }

    setState({ conversationLog: result.conversationLog, isLoading: false, error: null });
    return result.conversationLog;
  }, []);

  const clearConversationLog = useCallback(() => {
    setState({ conversationLog: null, isLoading: false, error: null });
  }, []);

  const setConversationLog = useCallback((conversationLog: ConversationLog | null) => {
    setState((prev) => ({ ...prev, conversationLog, error: null }));
  }, []);

  return {
    ...state,
    requestConversationLog,
    clearConversationLog,
    setConversationLog,
  };
}
