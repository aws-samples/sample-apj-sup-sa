/**
 * TaskList Component
 * 
 * Task 목록 표시 컴포넌트입니다.
 * 로딩/에러/빈 상태 처리를 포함합니다.
 * 
 * ORCH-003: MeetingPrepModal 분리 - Task 목록 UI를 별도 컴포넌트로 분리
 */

import type { TaskInfo } from '@shared/types/meeting-prep';

const WHITESPACE_REGEX = /\s+/g;

export interface TaskListProps {
  tasks: TaskInfo[];
  isLoading: boolean;
  error: string | null;
}

/**
 * Task 목록 컴포넌트
 * Requirements: 6.3, 6.4, 6.5
 */
function TaskList({ tasks, isLoading, error }: TaskListProps) {
  return (
    <div className="selected-opportunity-tasks">
      <h4 className="tasks-section-title">
        <span className="material-symbols-outlined">task</span>
        연결된 Task
      </h4>

      {/* Task 로딩 중 */}
      {isLoading && (
        <div className="tasks-loading">
          <span className="material-symbols-outlined tasks-loading-icon">sync</span>
          <span>Task 조회 중...</span>
        </div>
      )}

      {/* Task 조회 에러 */}
      {error && !isLoading && (
        <div className="tasks-error">
          <span className="material-symbols-outlined">error</span>
          <span>{error}</span>
        </div>
      )}

      {/* Task 없음 */}
      {!isLoading && !error && tasks.length === 0 && (
        <div className="tasks-empty">
          <span className="material-symbols-outlined">inbox</span>
          <span>연결된 Task가 없습니다</span>
        </div>
      )}

      {/* Task 목록 (Requirements: 6.5) */}
      {!isLoading && !error && tasks.length > 0 && (
        <ul className="tasks-list">
          {tasks.map((task) => (
            <li key={task.id} className="task-item">
              <div className="task-main">
                <span className="task-subject">{task.subject}</span>
                <span
                  className={`task-status status-${task.status?.toLowerCase().replace(WHITESPACE_REGEX, '-')}`}
                >
                  {task.status}
                </span>
              </div>
              {task.activityDate && (
                <div className="task-date">
                  <span className="material-symbols-outlined">event</span>
                  {task.activityDate}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default TaskList;
