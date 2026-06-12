/**
 * useAgentChat
 *
 * 미팅 종료 후 회의록 대화 agent와의 텍스트 채팅 상태를 관리한다.
 *  - send: 사용자 메시지 전송 → assistant 응답 + pendingActions 머지
 *  - resolve: pendingAction 승인/취소 → 부수효과 실행(회의록 수정/SFDC 로깅)
 *  - 회의록 수정은 main이 SUMMARY_COMPLETE/CONVERSATION_LOG_COMPLETE를 재emit하므로
 *    상위(useSummary/useConversationLog)가 탭을 자동 갱신한다 — 이 훅은 채팅만 담당.
 *
 * meetingId가 바뀌면 세션을 리셋한다.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { AgentChatMessage, AgentPendingAction } from '../../shared/types/agent';

interface AgentChatState {
  messages: AgentChatMessage[];
  pendingActions: AgentPendingAction[];
  isSending: boolean;
  error: string | null;
  mcpConnected: boolean;
}

const INITIAL: AgentChatState = {
  messages: [],
  pendingActions: [],
  isSending: false,
  error: null,
  mcpConnected: false,
};

export function useAgentChat(meetingId: string | null | undefined) {
  const [state, setState] = useState<AgentChatState>(INITIAL);
  // 최신 meetingId를 ref로 잡아 send/resolve가 stale closure를 안 쓰게 한다.
  const meetingIdRef = useRef(meetingId);
  meetingIdRef.current = meetingId;

  // meetingId 변경 시 로컬 상태 + main 세션 리셋.
  useEffect(() => {
    setState(INITIAL);
    if (meetingId && window.electronAPI?.agent) {
      void window.electronAPI.agent.reset({ meetingId });
    }
  }, [meetingId]);

  const send = useCallback(async (text: string) => {
    const id = meetingIdRef.current;
    const trimmed = text.trim();
    if (!id || !trimmed || !window.electronAPI?.agent) return;

    setState((prev) => ({
      ...prev,
      isSending: true,
      error: null,
      messages: [...prev.messages, { role: 'user', text: trimmed }],
    }));

    const res = await window.electronAPI.agent.chatSend({ meetingId: id, text: trimmed });
    if (!res.success) {
      setState((prev) => ({
        ...prev,
        isSending: false,
        error: res.error,
        messages: [...prev.messages, { role: 'system', text: `오류: ${res.error}` }],
      }));
      return;
    }

    const { assistantText, pendingActions, mcpConnected } = res.data;
    setState((prev) => ({
      ...prev,
      isSending: false,
      mcpConnected,
      messages: assistantText
        ? [...prev.messages, { role: 'assistant', text: assistantText }]
        : prev.messages,
      pendingActions: [...prev.pendingActions, ...pendingActions],
    }));
  }, []);

  const resolve = useCallback(async (actionId: string, approved: boolean) => {
    const id = meetingIdRef.current;
    if (!id || !window.electronAPI?.agent) return;

    // 낙관적으로 카드 제거.
    setState((prev) => ({
      ...prev,
      pendingActions: prev.pendingActions.filter((a) => a.id !== actionId),
    }));

    const res = await window.electronAPI.agent.resolveAction({ meetingId: id, actionId, approved });
    const text = res.success ? res.data.resultText : `오류: ${res.error}`;
    setState((prev) => ({
      ...prev,
      messages: [...prev.messages, { role: 'system', text }],
    }));
  }, []);

  return {
    messages: state.messages,
    pendingActions: state.pendingActions,
    isSending: state.isSending,
    error: state.error,
    mcpConnected: state.mcpConnected,
    send,
    resolve,
  };
}

export default useAgentChat;
