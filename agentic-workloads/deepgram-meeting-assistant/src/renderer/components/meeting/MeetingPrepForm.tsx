/**
 * MeetingPrepForm Component
 * 
 * 미팅 기본 정보 입력 폼 컴포넌트입니다.
 * 
 * ORCH-003: MeetingPrepModal 분리 - 폼 UI를 별도 컴포넌트로 분리
 */

import type { FormState } from '../../hooks/useMeetingPrep';

export interface MeetingPrepFormProps {
  formState: FormState;
  onFieldChange: <K extends keyof FormState>(field: K, value: FormState[K]) => void;
}

/**
 * 미팅 기본 정보 입력 폼
 * Requirements: 3.1-3.5
 */
function MeetingPrepForm({ formState, onFieldChange }: MeetingPrepFormProps) {
  return (
    <div className="meeting-prep-form">
      {/* 고객사명 입력 필드 (Requirements: 3.1) */}
      <div className="form-field">
        <label htmlFor="prep-company">고객사명</label>
        <input
          id="prep-company"
          type="text"
          value={formState.company}
          onChange={(e) => onFieldChange('company', e.target.value)}
          placeholder="고객사명을 입력하세요"
        />
      </div>

      {/* 미팅 일자 선택 필드 (Requirements: 3.2) */}
      <div className="form-field">
        <label htmlFor="prep-meeting-date">미팅 일자</label>
        <input
          id="prep-meeting-date"
          type="date"
          value={formState.meetingDate}
          onChange={(e) => onFieldChange('meetingDate', e.target.value)}
        />
      </div>

      {/* 미팅 주제 입력 필드 (Requirements: 3.3) */}
      <div className="form-field">
        <label htmlFor="prep-meeting-topic">미팅 주제</label>
        <input
          id="prep-meeting-topic"
          type="text"
          value={formState.meetingTopic}
          onChange={(e) => onFieldChange('meetingTopic', e.target.value)}
          placeholder="미팅 주제를 입력하세요"
        />
      </div>

      {/* 참석자 정보 입력 필드 (Requirements: 3.4) */}
      <div className="form-field">
        <label htmlFor="prep-attendees">참석자</label>
        <input
          id="prep-attendees"
          type="text"
          value={formState.attendees}
          onChange={(e) => onFieldChange('attendees', e.target.value)}
          placeholder="참석자 정보를 입력하세요 (예: 홍길동, 김철수)"
        />
      </div>

      {/* 메모 입력 필드 (Requirements: 3.5) */}
      <div className="form-field">
        <label htmlFor="prep-note">메모</label>
        <textarea
          id="prep-note"
          value={formState.note}
          onChange={(e) => onFieldChange('note', e.target.value)}
          placeholder="미팅 관련 메모를 입력하세요"
          rows={3}
        />
      </div>
    </div>
  );
}

export default MeetingPrepForm;
