/**
 * SelectedOpportunityCard Component
 * 
 * 선택된 Opportunity의 상세 정보와 Task 목록을 표시합니다.
 * 
 * ORCH-003: MeetingPrepModal 분리 - 선택된 Opportunity UI를 별도 컴포넌트로 분리
 */

import type { OpportunityInfo, TaskInfo } from '@shared/types/meeting-prep';
import TaskList from './TaskList';

const WHITESPACE_REGEX = /\s+/g;

export interface SelectedOpportunityCardProps {
  opportunity: OpportunityInfo;
  tasks: TaskInfo[];
  isLoadingTasks: boolean;
  taskError: string | null;
  onClear: () => void;
}

/**
 * 선택된 Opportunity 상세 카드
 * Requirements: 6.2
 */
function SelectedOpportunityCard({
  opportunity,
  tasks,
  isLoadingTasks,
  taskError,
  onClear,
}: SelectedOpportunityCardProps) {
  return (
    <div className="meeting-prep-selected-opportunity">
      <div className="selected-opportunity-header">
        <h3 className="meeting-prep-section-title">
          <span className="material-symbols-outlined">check_circle</span>
          선택된 Opportunity
        </h3>
        <button
          type="button"
          className="selected-opportunity-clear"
          onClick={onClear}
          aria-label="선택 해제"
        >
          <span className="material-symbols-outlined">close</span>
        </button>
      </div>
      <div className="selected-opportunity-content">
        <div className="selected-opportunity-name">{opportunity.name}</div>
        <div className="selected-opportunity-details">
          <div className="selected-opportunity-detail-item">
            <span className="detail-label">단계</span>
            <span
              className={`search-result-stage stage-${opportunity.stageName?.toLowerCase().replace(WHITESPACE_REGEX, '-')}`}
            >
              {opportunity.stageName}
            </span>
          </div>
          <div className="selected-opportunity-detail-item">
            <span className="detail-label">고객사</span>
            <span className="detail-value">
              {opportunity.account?.name || opportunity.accountName || '-'}
            </span>
          </div>
          <div className="selected-opportunity-detail-item">
            <span className="detail-label">담당자</span>
            <span className="detail-value">{opportunity.owner?.name || '-'}</span>
          </div>
          {opportunity.closeDate && (
            <div className="selected-opportunity-detail-item">
              <span className="detail-label">마감일</span>
              <span className="detail-value">{opportunity.closeDate}</span>
            </div>
          )}
        </div>

        {/* Task 목록 */}
        <TaskList tasks={tasks} isLoading={isLoadingTasks} error={taskError} />
      </div>
    </div>
  );
}

export default SelectedOpportunityCard;
