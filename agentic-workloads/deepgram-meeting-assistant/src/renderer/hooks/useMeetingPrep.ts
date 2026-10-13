/**
 * useMeetingPrep Hook
 * 
 * 미팅 준비 모달의 상태 관리를 담당하는 커스텀 훅입니다.
 * 폼, MCP 연결, Opportunity 검색, Task 조회 상태를 그룹화하여 관리합니다.
 * 
 * ORCH-022: Excessive State Variables → 상태 그룹화 및 커스텀 훅으로 분리
 */

import { useEffect, useCallback, useState, useRef, useReducer } from 'react';
import type {
  MeetingPrepData,
  OpportunityInfo,
  OpportunitySearchResult,
  TaskInfo,
  TaskSearchResult,
} from '@shared/types/meeting-prep';
import type { ConnectionStatus } from '@shared/types/mcp';
import { getElectronAPI } from '../utils/electron';

// ============================================================================
// Types
// ============================================================================

/**
 * 폼 상태
 */
export interface FormState {
  company: string;
  meetingDate: string;
  meetingTopic: string;
  attendees: string;
  note: string;
}

/**
 * MCP 연결 상태
 */
export interface McpState {
  status: ConnectionStatus;
  error: string | null;
}

/**
 * Opportunity 검색 상태
 */
export interface SearchState {
  accountIdInput: string;
  userAlias: string;
  isSearching: boolean;
  results: OpportunityInfo[];
  error: string | null;
  hasSearched: boolean;
  hasNextPage: boolean;
  cursor: string | null;
  isLoadingMore: boolean;
}

/**
 * 선택 및 Task 상태
 */
export interface SelectionState {
  selectedOpportunity: OpportunityInfo | null;
  tasks: TaskInfo[];
  isLoadingTasks: boolean;
  taskError: string | null;
}

/**
 * 검색 액션 타입
 */
type SearchAction =
  | { type: 'SET_ACCOUNT_ID'; payload: string }
  | { type: 'SET_USER_ALIAS'; payload: string }
  | { type: 'START_SEARCH'; isNewSearch: boolean }
  | { type: 'SEARCH_SUCCESS'; payload: { results: OpportunityInfo[]; hasNextPage: boolean; cursor: string | null; append: boolean } }
  | { type: 'SEARCH_ERROR'; payload: string }
  | { type: 'RESET_SEARCH' };

// ============================================================================
// Helpers
// ============================================================================

/**
 * 오늘 날짜를 YYYY-MM-DD 형식으로 반환
 */
export function getTodayDateString(): string {
  const today = new Date();
  const year = today.getFullYear();
  const month = String(today.getMonth() + 1).padStart(2, '0');
  const day = String(today.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

// ============================================================================
// Reducers
// ============================================================================

const initialSearchState: SearchState = {
  accountIdInput: '',
  userAlias: '',
  isSearching: false,
  results: [],
  error: null,
  hasSearched: false,
  hasNextPage: false,
  cursor: null,
  isLoadingMore: false,
};

function searchReducer(state: SearchState, action: SearchAction): SearchState {
  switch (action.type) {
    case 'SET_ACCOUNT_ID':
      return { ...state, accountIdInput: action.payload };
    case 'SET_USER_ALIAS':
      return { ...state, userAlias: action.payload };
    case 'START_SEARCH':
      if (action.isNewSearch) {
        return {
          ...state,
          isSearching: true,
          results: [],
          cursor: null,
          hasNextPage: false,
          error: null,
          hasSearched: true,
        };
      }
      return { ...state, isLoadingMore: true, error: null };
    case 'SEARCH_SUCCESS':
      return {
        ...state,
        isSearching: false,
        isLoadingMore: false,
        results: action.payload.append
          ? [...state.results, ...action.payload.results]
          : action.payload.results,
        hasNextPage: action.payload.hasNextPage,
        cursor: action.payload.cursor,
      };
    case 'SEARCH_ERROR':
      return {
        ...state,
        isSearching: false,
        isLoadingMore: false,
        error: action.payload,
      };
    case 'RESET_SEARCH':
      return initialSearchState;
    default:
      return state;
  }
}

// ============================================================================
// Hook
// ============================================================================

export interface UseMeetingPrepOptions {
  initialData?: MeetingPrepData;
  isOpen: boolean;
}

export interface UseMeetingPrepReturn {
  // Form state
  formState: FormState;
  setFormField: <K extends keyof FormState>(field: K, value: FormState[K]) => void;
  
  // MCP state
  mcpState: McpState;
  connectMcpServer: () => Promise<void>;
  
  // Search state
  searchState: SearchState;
  setAccountIdInput: (value: string) => void;
  setUserAlias: (value: string) => void;
  handleSearchOpportunities: (cursor?: string | null) => Promise<void>;
  
  // Selection state
  selectionState: SelectionState;
  handleSelectOpportunity: (opportunity: OpportunityInfo) => void;
  handleClearSelectedOpportunity: () => void;
  
  // Data collection
  collectPrepData: () => MeetingPrepData;
  
  // MCP response parser
  parseMcpToolResponse: <T>(content: unknown) => T | null;
}

export function useMeetingPrep({
  initialData,
  isOpen,
}: UseMeetingPrepOptions): UseMeetingPrepReturn {
  // ==================== Form State ====================
  const [formState, setFormState] = useState<FormState>({
    company: initialData?.company ?? '',
    meetingDate: initialData?.meetingDate ?? getTodayDateString(),
    meetingTopic: initialData?.meetingTopic ?? '',
    attendees: initialData?.attendees ?? '',
    note: initialData?.note ?? '',
  });

  const setFormField = useCallback(<K extends keyof FormState>(field: K, value: FormState[K]) => {
    setFormState((prev) => ({ ...prev, [field]: value }));
  }, []);

  // ==================== MCP State ====================
  const [mcpState, setMcpState] = useState<McpState>({
    status: 'disconnected',
    error: null,
  });
  const hasAttemptedAutoConnect = useRef(false);

  const connectMcpServer = useCallback(async () => {
    const electronAPI = getElectronAPI();
    if (!electronAPI) {
      setMcpState({ status: 'error', error: 'Electron 환경에서만 사용할 수 있습니다.' });
      return;
    }
    
    if (mcpState.status === 'connected' || mcpState.status === 'connecting') {
      return;
    }

    setMcpState({ status: 'connecting', error: null });

    try {
      const result = await electronAPI.mcp.connect();
      if (result.success) {
        setMcpState({ status: 'connected', error: null });
      } else {
        setMcpState({
          status: 'error',
          error: result.error || 'MCP 서버 연결에 실패했습니다.',
        });
      }
    } catch (err) {
      setMcpState({
        status: 'error',
        error: err instanceof Error ? err.message : 'MCP 서버 연결 중 오류가 발생했습니다.',
      });
    }
  }, [mcpState.status]);

  // ==================== Search State ====================
  const [searchState, dispatchSearch] = useReducer(searchReducer, {
    ...initialSearchState,
    userAlias: '',
  });

  const setAccountIdInput = useCallback((value: string) => {
    dispatchSearch({ type: 'SET_ACCOUNT_ID', payload: value });
  }, []);

  const setUserAlias = useCallback((value: string) => {
    dispatchSearch({ type: 'SET_USER_ALIAS', payload: value });
  }, []);

  // ==================== Selection State ====================
  const [selectionState, setSelectionState] = useState<SelectionState>({
    selectedOpportunity: initialData?.selectedOpportunity ?? null,
    tasks: initialData?.tasks ?? [],
    isLoadingTasks: false,
    taskError: null,
  });

  // ==================== MCP Response Parser ====================
  const parseMcpToolResponse = useCallback(<T,>(content: unknown): T | null => {
    try {
      if (Array.isArray(content)) {
        const textContent = content.find(
          (item): item is { type: string; text: string } =>
            typeof item === 'object' &&
            item !== null &&
            'type' in item &&
            item.type === 'text' &&
            'text' in item
        );
        if (textContent?.text) {
          const parsed = JSON.parse(textContent.text);
          if (parsed && typeof parsed === 'object' && 'data' in parsed) {
            return parsed.data as T;
          }
          return parsed as T;
        }
      }
      return null;
    } catch {
      return null;
    }
  }, []);

  // ==================== Task Fetching ====================
  const fetchTasksForOpportunity = useCallback(
    async (opportunityId: string) => {
      if (!searchState.userAlias.trim()) {
        setSelectionState((prev) => ({
          ...prev,
          taskError: 'Task를 조회하려면 사용자 Alias를 입력하세요.',
          tasks: [],
        }));
        return;
      }

      const electronAPI = getElectronAPI();
      if (!electronAPI) {
        setSelectionState((prev) => ({
          ...prev,
          taskError: 'Electron 환경에서만 사용할 수 있습니다.',
        }));
        return;
      }

      setSelectionState((prev) => ({
        ...prev,
        isLoadingTasks: true,
        taskError: null,
        tasks: [],
      }));

      try {
        const result = await electronAPI.mcp.callTool('list_user_tasks', {
          userAlias: searchState.userAlias.trim(),
          opportunityId,
          limit: 50,
        });

        if (result.success && result.data) {
          const taskResult = parseMcpToolResponse<TaskSearchResult>(result.data.content);

          if (taskResult && Array.isArray(taskResult.tasks)) {
            setSelectionState((prev) => ({
              ...prev,
              isLoadingTasks: false,
              tasks: taskResult.tasks,
            }));
          } else if (taskResult && Array.isArray(taskResult)) {
            setSelectionState((prev) => ({
              ...prev,
              isLoadingTasks: false,
              tasks: taskResult as unknown as TaskInfo[],
            }));
          } else {
            setSelectionState((prev) => ({
              ...prev,
              isLoadingTasks: false,
              tasks: [],
            }));
          }
        } else {
          setSelectionState((prev) => ({
            ...prev,
            isLoadingTasks: false,
            taskError: result.error || 'Task 조회 중 오류가 발생했습니다.',
          }));
        }
      } catch (err) {
        setSelectionState((prev) => ({
          ...prev,
          isLoadingTasks: false,
          taskError: err instanceof Error ? err.message : 'Task 조회 중 오류가 발생했습니다.',
        }));
      }
    },
    [searchState.userAlias, parseMcpToolResponse]
  );

  // ==================== Search Handlers ====================
  const handleSearchOpportunities = useCallback(
    async (cursor?: string | null) => {
      const electronAPI = getElectronAPI();
      if (!electronAPI) {
        dispatchSearch({ type: 'SEARCH_ERROR', payload: 'Electron 환경에서만 사용할 수 있습니다.' });
        return;
      }

      const trimmedAccountId = searchState.accountIdInput.trim();
      if (!trimmedAccountId) {
        return;
      }

      dispatchSearch({ type: 'START_SEARCH', isNewSearch: !cursor });

      try {
        const result = await electronAPI.mcp.callTool('search_opportunities', {
          condition: {
            operator: 'AND',
            conditions: [
              {
                field: 'accountId',
                operator: 'EXACT_MATCH',
                value: trimmedAccountId,
              },
              {
                operator: 'OR',
                conditions: [
                  { field: 'stageName', operator: 'EXACT_MATCH', value: 'Prospect' },
                  { field: 'stageName', operator: 'EXACT_MATCH', value: 'Qualified' },
                  { field: 'stageName', operator: 'EXACT_MATCH', value: 'Technical Validation' },
                  { field: 'stageName', operator: 'EXACT_MATCH', value: 'Business Validation' },
                ],
              },
            ],
          },
          limit: 10,
          ...(cursor ? { after: cursor } : {}),
        });

        if (result.success && result.data) {
          const searchResult = parseMcpToolResponse<OpportunitySearchResult>(result.data.content);

          if (searchResult && searchResult.opportunities) {
            dispatchSearch({
              type: 'SEARCH_SUCCESS',
              payload: {
                results: searchResult.opportunities,
                hasNextPage: searchResult.hasNextPage,
                cursor: searchResult.cursor,
                append: !!cursor,
              },
            });
          } else {
            dispatchSearch({
              type: 'SEARCH_SUCCESS',
              payload: {
                results: [],
                hasNextPage: false,
                cursor: null,
                append: !!cursor,
              },
            });
          }
        } else {
          dispatchSearch({
            type: 'SEARCH_ERROR',
            payload: result.error || '검색 중 오류가 발생했습니다.',
          });
        }
      } catch (err) {
        dispatchSearch({
          type: 'SEARCH_ERROR',
          payload: err instanceof Error ? err.message : '검색 중 오류가 발생했습니다.',
        });
      }
    },
    [searchState.accountIdInput, parseMcpToolResponse]
  );

  // ==================== Selection Handlers ====================
  const handleSelectOpportunity = useCallback(
    (opportunity: OpportunityInfo) => {
      if (selectionState.selectedOpportunity?.id === opportunity.id) {
        setSelectionState((prev) => ({
          ...prev,
          selectedOpportunity: null,
          tasks: [],
          taskError: null,
        }));
      } else {
        setSelectionState((prev) => ({
          ...prev,
          selectedOpportunity: opportunity,
        }));
        fetchTasksForOpportunity(opportunity.id);
      }
    },
    [selectionState.selectedOpportunity, fetchTasksForOpportunity]
  );

  const handleClearSelectedOpportunity = useCallback(() => {
    setSelectionState((prev) => ({
      ...prev,
      selectedOpportunity: null,
      tasks: [],
      taskError: null,
    }));
  }, []);

  // ==================== Data Collection ====================
  const collectPrepData = useCallback((): MeetingPrepData => {
    return {
      company: formState.company,
      meetingDate: formState.meetingDate,
      meetingTopic: formState.meetingTopic,
      attendees: formState.attendees,
      note: formState.note,
      selectedOpportunity: selectionState.selectedOpportunity,
      tasks: selectionState.tasks,
    };
  }, [formState, selectionState.selectedOpportunity, selectionState.tasks]);

  // ==================== Effects ====================

  // Update form when initialData changes
  useEffect(() => {
    if (initialData) {
      setFormState({
        company: initialData.company,
        meetingDate: initialData.meetingDate || getTodayDateString(),
        meetingTopic: initialData.meetingTopic,
        attendees: initialData.attendees,
        note: initialData.note,
      });
      setSelectionState((prev) => ({
        ...prev,
        selectedOpportunity: initialData.selectedOpportunity ?? null,
        tasks: initialData.tasks ?? [],
      }));
    }
  }, [initialData]);

  // Auto-connect MCP on modal open
  useEffect(() => {
    if (!isOpen) {
      hasAttemptedAutoConnect.current = false;
      return;
    }

    const electronAPI = getElectronAPI();
    if (!electronAPI) {
      setMcpState({ status: 'error', error: 'Electron 환경에서만 사용할 수 있습니다.' });
      return;
    }

    const checkAndConnect = async () => {
      const status = await electronAPI.mcp.getStatus();
      setMcpState((prev) => ({ ...prev, status }));

      if (status !== 'connected' && !hasAttemptedAutoConnect.current) {
        hasAttemptedAutoConnect.current = true;
        setMcpState({ status: 'connecting', error: null });

        try {
          const result = await electronAPI.mcp.connect();
          if (result.success) {
            setMcpState({ status: 'connected', error: null });
          } else {
            setMcpState({
              status: 'error',
              error: result.error || 'MCP 서버 연결에 실패했습니다.',
            });
          }
        } catch (err) {
          setMcpState({
            status: 'error',
            error: err instanceof Error ? err.message : 'MCP 서버 연결 중 오류가 발생했습니다.',
          });
        }
      }
    };

    checkAndConnect();
  }, [isOpen]);

  return {
    formState,
    setFormField,
    mcpState,
    connectMcpServer,
    searchState,
    setAccountIdInput,
    setUserAlias,
    handleSearchOpportunities,
    selectionState,
    handleSelectOpportunity,
    handleClearSelectedOpportunity,
    collectPrepData,
    parseMcpToolResponse,
  };
}
