/**
 * MeetingPrepModal Component
 * 
 * 미팅 준비 모달 컴포넌트입니다.
 * 분리된 하위 컴포넌트들과 커스텀 훅을 조합하여 구성합니다.
 * 
 * ORCH-003, ORCH-022: Large Component → 컴포넌트 분리 및 상태 관리 개선
 * 
 * Requirements:
 * - 1.1: 화면 중앙에 오버레이로 표시
 * - 1.3: 모달 외부 클릭 또는 ESC 키로 닫기
 * - 1.4: 닫기 버튼으로 닫기
 * - 2.1-2.4: MCP 서버 자동 연결
 * - 3.1-3.5: 미팅 기본 정보 입력 필드
 * - 4.1-4.6: Opportunity 검색
 * - 5.1-5.4: 페이지네이션
 * - 6.1-6.5: Opportunity 선택 및 Task 조회
 * - 7.1-7.3: 완료 및 데이터 저장
 */

import { useEffect, useCallback } from 'react';
import type { MeetingPrepData } from '@shared/types/meeting-prep';
import { useMeetingPrep } from '../../hooks/useMeetingPrep';
import MeetingPrepForm from './MeetingPrepForm';
import OpportunitySearch from './OpportunitySearch';
import SelectedOpportunityCard from './SelectedOpportunityCard';

/**
 * MeetingPrepModal Props
 */
export interface MeetingPrepModalProps {
  isOpen: boolean;
  onClose: () => void;
  onComplete: (prepData: MeetingPrepData) => void;
  initialData?: MeetingPrepData;
}

/**
 * 미팅 준비 모달 컴포넌트
 */
function MeetingPrepModal({
  isOpen,
  onClose,
  onComplete,
  initialData,
}: MeetingPrepModalProps) {
  const {
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
  } = useMeetingPrep({ initialData, isOpen });

  // ESC 키 핸들링 (Requirements: 1.3)
  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    },
    [onClose]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'hidden';
    }

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleKeyDown]);

  // 모달 외부 클릭 핸들링 (Requirements: 1.3)
  const handleOverlayClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      onClose();
    }
  };

  // 완료 버튼 클릭 핸들러 (Requirements: 7.1, 7.3)
  const handleComplete = useCallback(() => {
    const prepData = collectPrepData();
    onComplete(prepData);
    onClose();
  }, [collectPrepData, onComplete, onClose]);

  if (!isOpen) {
    return null;
  }

  return (
    <div className="meeting-prep-modal-overlay" onClick={handleOverlayClick}>
      <div className="meeting-prep-modal">
        {/* 모달 헤더 */}
        <div className="meeting-prep-modal-header">
          <h2>미팅 준비</h2>
          {/* 닫기 버튼 (Requirements: 1.4) */}
          <button
            type="button"
            className="meeting-prep-modal-close"
            onClick={onClose}
            aria-label="닫기"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        {/* 모달 본문 */}
        <div className="meeting-prep-modal-body">
          {/* 미팅 기본 정보 입력 폼 (Requirements: 3.1-3.5) */}
          <MeetingPrepForm
            formState={formState}
            onFieldChange={setFormField}
          />

          {/* Opportunity 검색 섹션 (Requirements: 4.1-4.6, 5.1-5.4) */}
          <OpportunitySearch
            mcpState={mcpState}
            searchState={searchState}
            selectedOpportunityId={selectionState.selectedOpportunity?.id ?? null}
            onConnectMcp={connectMcpServer}
            onAccountIdChange={setAccountIdInput}
            onUserAliasChange={setUserAlias}
            onSearch={handleSearchOpportunities}
            onSelectOpportunity={handleSelectOpportunity}
          />

          {/* 선택된 Opportunity 상세 영역 (Requirements: 6.2-6.5) */}
          {selectionState.selectedOpportunity && (
            <SelectedOpportunityCard
              opportunity={selectionState.selectedOpportunity}
              tasks={selectionState.tasks}
              isLoadingTasks={selectionState.isLoadingTasks}
              taskError={selectionState.taskError}
              onClear={handleClearSelectedOpportunity}
            />
          )}
        </div>

        {/* 모달 푸터 */}
        <div className="meeting-prep-modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
          >
            취소
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleComplete}
          >
            완료
          </button>
        </div>
      </div>
    </div>
  );
}

export default MeetingPrepModal;
