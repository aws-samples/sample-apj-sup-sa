import { MEETING_TYPES, MeetingType, MeetingTypeConfig } from '../../shared/types';

interface MeetingTypeSelectorProps {
  onSelect: (type: MeetingType) => void;
}

function MeetingTypeSelector({ onSelect }: MeetingTypeSelectorProps) {
  return (
    <div className="meeting-selector">
      <div className="section-header">
        <h1>Start a New Meeting</h1>
        <p>Select a specialized template for AI-powered transcription</p>
      </div>
      
      <div className="meeting-grid">
        {MEETING_TYPES.map((meetingType: MeetingTypeConfig) => (
          <div key={meetingType.id} className="meeting-card">
            <div className={`card-icon ${meetingType.bgColor} ${meetingType.textColor}`}>
              <span className="material-symbols-outlined">{meetingType.icon}</span>
            </div>
            <h2>{meetingType.label}</h2>
            <p>{meetingType.description}</p>
            <button 
              type="button"
              className="start-button"
              onClick={() => onSelect(meetingType.id)}
              aria-label={`${meetingType.label} 미팅 시작`}
            >
              <span>Start</span>
              <span className="material-symbols-outlined">arrow_forward</span>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export default MeetingTypeSelector;
