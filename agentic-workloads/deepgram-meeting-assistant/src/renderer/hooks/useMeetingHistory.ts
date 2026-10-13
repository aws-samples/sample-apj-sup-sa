import { useState, useCallback, useEffect } from 'react';
import type { Meeting } from '@shared/types';

const DEFAULT_HISTORY_LIMIT = 20;

interface MeetingHistoryState {
  meetings: Meeting[];
  isLoading: boolean;
  error: string | null;
  hasMore: boolean;
  offset: number;
}

export function useMeetingHistory(limit = DEFAULT_HISTORY_LIMIT) {
  const [state, setState] = useState<MeetingHistoryState>({
    meetings: [],
    isLoading: false,
    error: null,
    hasMore: true,
    offset: 0,
  });

  const loadMeetings = useCallback(
    async (reset = false) => {
      if (!window.electronAPI) {
        setState((prev) => ({ ...prev, meetings: [], isLoading: false }));
        return;
      }

      const currentOffset = reset ? 0 : state.offset;

      // 이미 로딩 중이면 중복 호출 방지 (단, 리셋일 경우는 강제 진행 가능)
      if (state.isLoading && !reset) return;

      // 로딩 표시 여부 결정:
      // 1. 더 보기(load more, !reset)인 경우: 로딩 표시
      // 2. 데이터가 없는 초기 로딩인 경우: 로딩 표시
      // 3. 이미 데이터가 있는데 새로고침(reset)하는 경우: 깜빡임 방지를 위해 로딩 표시 생략
      const shouldShowLoading = !reset || state.meetings.length === 0;

      if (shouldShowLoading) {
        setState((prev) => ({ ...prev, isLoading: true, error: null }));
      }

      const result = await window.electronAPI.listMeetings({
        limit,
        offset: currentOffset,
      });

      if (!result.success) {
        setState((prev) => ({
          ...prev,
          isLoading: false,
          error: result.error || '미팅 목록을 불러오지 못했습니다.',
        }));
        return;
      }

      const newMeetings = result.meetings ?? [];
      const hasMore = newMeetings.length === limit;

      setState((prev) => ({
        meetings: reset ? newMeetings : [...prev.meetings, ...newMeetings],
        isLoading: false,
        error: null,
        hasMore,
        offset: currentOffset + newMeetings.length,
      }));
    },
    [limit, state.offset, state.isLoading, state.meetings.length]
  );

  // 초기 로딩
  useEffect(() => {
    loadMeetings(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // 의존성 배열을 비워 초기 1회만 실행되도록 함 (limit 변경 시는 refresh 호출 권장)

  const refresh = useCallback(() => {
    loadMeetings(true);
  }, [loadMeetings]);

  const loadMore = useCallback(() => {
    if (!state.hasMore || state.isLoading) return;
    loadMeetings(false);
  }, [state.hasMore, state.isLoading, loadMeetings]);

  return {
    meetings: state.meetings,
    isLoading: state.isLoading,
    error: state.error,
    hasMore: state.hasMore,
    refresh,
    loadMore,
  };
}