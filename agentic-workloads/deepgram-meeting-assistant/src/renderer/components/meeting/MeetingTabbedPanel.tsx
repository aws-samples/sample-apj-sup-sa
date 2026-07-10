import { type ReactNode, useMemo } from 'react';
import Markdown from 'react-markdown';
import QuickMeetingTranscript from '../QuickMeetingTranscript';
import copyIcon from '../../assets/images/copy.png';
import sortIcon from '../../assets/images/sort.png';
import { normalizeErrorMessage } from '../../utils/normalize-error';
import type { BaseMeetingViewProps, QuickMeetingTab } from '../meeting-types/types';
import type { MeetingPrepData } from '@shared/types/meeting-prep';
import type { ConversationLog } from '@shared/types/meeting';

interface MeetingTabbedPanelProps extends BaseMeetingViewProps {
  activeTab: QuickMeetingTab;
  onTabChange: (tab: QuickMeetingTab) => void;
  onCopyNotes: () => void;
  isReverseScript: boolean;
  onToggleReverseScript: () => void;
  meetingTitle: string;
  onTitleChange: (title: string) => void;
  meetingLabel: string;
  onBack: () => void;
  rightPanelContent: ReactNode;
  isRightPanelOpen: boolean;
  onToggleRightPanel: () => void;
  /** 미팅 준비 데이터 (Requirements: 7.2, 8.1) */
  prepData?: MeetingPrepData | null;
  isViewingHistory?: boolean;
  isSummaryLoading?: boolean;
  onRequestSummary?: (meetingId: string) => Promise<unknown>;
  /** 대화 로그 */
  conversationLog?: ConversationLog | null;
  isConversationLogLoading?: boolean;
  conversationLogError?: string | null;
  onRequestConversationLog?: (meetingId: string) => Promise<unknown>;
}

function MeetingTabbedPanel({
  recordingState,
  segments,
  correctedSentences,
  partialText,
  partialSpeaker,
  summary,
  fullScript,
  transcriptionError,
  audioError,
  summaryError,
  activeTab,
  onTabChange,
  onCopyNotes,
  isReverseScript,
  onToggleReverseScript,
  meetingTitle,
  onTitleChange,
  meetingLabel,
  onBack,
  rightPanelContent,
  isRightPanelOpen,
  onToggleRightPanel,
  prepData,
  isViewingHistory = false,
  isSummaryLoading = false,
  onRequestSummary,
  conversationLog,
  isConversationLogLoading = false,
  conversationLogError,
  onRequestConversationLog,
}: MeetingTabbedPanelProps) {
  const isCompleted = recordingState.status === 'completed';

  const hasScript = fullScript.trim().length > 0;
  const canCopy = activeTab === 'summary'
    ? Boolean(summary)
    : activeTab === 'conversation'
    ? Boolean(conversationLog && conversationLog.topics.length > 0)
    : hasScript;

  const handleRequestSummary = () => {
    if (recordingState.meetingId && onRequestSummary) {
      onRequestSummary(recordingState.meetingId);
    }
  };

  const handleRequestConversationLog = () => {
    if (recordingState.meetingId && onRequestConversationLog) {
      onRequestConversationLog(recordingState.meetingId);
    }
  };

  const summaryMarkdown = useMemo(() => {
    if (!summary) return null;
    
    const sections: string[] = [];
    
    if (summary.mainTopics.length > 0) {
      sections.push('## 주요 논의 주제\n' + summary.mainTopics.map(t => `- ${t}`).join('\n'));
    }
    
    if (summary.topicDiscussions.length > 0) {
      const discParts = summary.topicDiscussions.map(d => {
        let text = `### ${d.topic}\n`;
        if (d.discussions.length > 0) {
          text += d.discussions.map(item => `- ${item}`).join('\n');
        }
        if (d.decisions.length > 0) {
          text += '\n\n**결정사항:**\n' + d.decisions.map(dec => `- ✅ ${dec}`).join('\n');
        }
        return text;
      });
      sections.push('## 논의 및 결정 사항\n' + discParts.join('\n\n'));
    }
    
    if (summary.keyTakeaways.length > 0) {
      sections.push('## 핵심 요약\n' + summary.keyTakeaways.map(t => `- ${t}`).join('\n'));
    }
    
    if (summary.confirmedActions.length > 0) {
      sections.push('## 확정된 액션 아이템\n' + summary.confirmedActions.map(a => 
        `- **${a.task}**\n  - 담당: ${a.owner}\n  - 기한: ${a.deadline}`
      ).join('\n'));
    }
    
    if (summary.pendingActions.length > 0) {
      sections.push('## 확인 필요 액션 아이템\n' + summary.pendingActions.map(a => 
        `- **${a.task}**\n  - 담당: ${a.owner}\n  - 기한: ${a.deadline}`
      ).join('\n'));
    }
    
    if (summary.followUps.length > 0) {
      sections.push('## 후속 조치\n' + summary.followUps.map(f => `- ${f}`).join('\n'));
    }
    
    if (summary.openIssues.length > 0) {
      sections.push('## 미해결 이슈\n' + summary.openIssues.map(i => `- ⚠️ ${i}`).join('\n'));
    }
    
    return sections.join('\n\n');
  }, [summary]);

  const conversationLogMarkdown = useMemo(() => {
    if (!conversationLog || conversationLog.topics.length === 0) return null;
    
    const sections: string[] = conversationLog.topics.map(topic => {
      const points = topic.points.map(point => `- ${point}`).join('\n');
      return `### ${topic.title}\n${points}`;
    });
    
    return sections.join('\n\n');
  }, [conversationLog]);

  const renderConversationLogEmptyState = () => {
    if (isConversationLogLoading) {
      return (
        <div className="qm-empty-state">
          <span className="material-symbols-outlined qm-loading-icon">hourglass_empty</span>
          <p>대화 요약을 생성하고 있습니다...</p>
        </div>
      );
    }

    if (isViewingHistory) {
      return (
        <div className="qm-empty-state">
          <span className="material-symbols-outlined qm-empty-icon">format_list_bulleted</span>
          <p>저장된 대화 요약이 없습니다.</p>
          <button 
            type="button" 
            className="qm-summary-action-btn"
            onClick={handleRequestConversationLog}
          >
            대화 요약 생성하기
          </button>
        </div>
      );
    }

    if (isCompleted) {
      return (
        <div className="qm-empty-state">
          <p>대화 요약 생성 중 오류가 발생했거나 내용이 너무 짧습니다.</p>
          <button 
            type="button" 
            className="qm-summary-action-btn"
            onClick={handleRequestConversationLog}
          >
            다시 시도
          </button>
        </div>
      );
    }

    return (
      <div className="qm-empty-state">
        <p>녹음이 완료되면 대화 요약이 자동으로 생성됩니다.</p>
      </div>
    );
  };

  const renderSummaryEmptyState = () => {
    if (isSummaryLoading) {
      return (
        <div className="qm-empty-state">
          <span className="material-symbols-outlined qm-loading-icon">hourglass_empty</span>
          <p>AI 회의록을 생성하고 있습니다...</p>
        </div>
      );
    }

    if (isViewingHistory) {
      return (
        <div className="qm-empty-state">
          <span className="material-symbols-outlined qm-empty-icon">summarize</span>
          <p>저장된 AI 회의록이 없습니다.</p>
          <button 
            type="button" 
            className="qm-summary-action-btn"
            onClick={handleRequestSummary}
          >
            AI 회의록 생성하기
          </button>
        </div>
      );
    }

    if (isCompleted) {
      // 로딩 중이 아닌데 요약이 없는 경우 (예: 오류 발생 등)
      return (
        <div className="qm-empty-state">
          <p>요약 생성 중 오류가 발생했거나 내용이 너무 짧습니다.</p>
          <button 
            type="button" 
            className="qm-summary-action-btn"
            onClick={handleRequestSummary}
          >
            다시 시도
          </button>
        </div>
      );
    }

    return (
      <div className="qm-empty-state">
        <p>녹음이 완료되면 AI 회의록이 자동으로 생성됩니다.</p>
      </div>
    );
  };

  return (
    <div className="qm-shell">
      <div className="qm-panel">
        <div className="qm-panel-header">
          <div className="qm-meta-row">
            <button type="button" className="back-button qm-back-button" onClick={onBack}>
              <span className="material-symbols-outlined">arrow_back</span>
              <span>Back</span>
            </button>
            <span className="qm-meeting-label">{meetingLabel}</span>
          </div>
          <div className="qm-title-row">
            <input
              className="qm-title-input"
              value={meetingTitle}
              onChange={(event) => onTitleChange(event.target.value)}
              onBlur={() => {
                if (!meetingTitle.trim()) {
                  onTitleChange('Untitled');
                }
              }}
              placeholder="Untitled"
              aria-label="회의 제목"
            />
          </div>
          <div className="qm-tab-row">
            <div className="qm-tabs" role="tablist" aria-label="Meeting tabs">
              <button
                type="button"
                className={`qm-tab-btn ${activeTab === 'script' ? 'active' : ''}`}
                onClick={() => onTabChange('script')}
                role="tab"
                aria-selected={activeTab === 'script'}
              >
                스크립트
              </button>
              <button
                type="button"
                className={`qm-tab-btn ${activeTab === 'conversation' ? 'active' : ''}`}
                onClick={() => onTabChange('conversation')}
                role="tab"
                aria-selected={activeTab === 'conversation'}
              >
                대화 요약
              </button>
              <button
                type="button"
                className={`qm-tab-btn ${activeTab === 'summary' ? 'active' : ''}`}
                onClick={() => onTabChange('summary')}
                role="tab"
                aria-selected={activeTab === 'summary'}
              >
                AI 회의록
              </button>
            </div>
            <div className="qm-tab-actions">
              <button
                type="button"
                className={`qm-order-btn ${isReverseScript ? 'active' : ''}`}
                onClick={onToggleReverseScript}
                disabled={activeTab !== 'script'}
                aria-label="스크립트 역순"
                title="스크립트 역순"
              >
                <img src={sortIcon} alt="" />
              </button>
              {activeTab === 'conversation' && (isViewingHistory || (conversationLog && conversationLog.topics.length > 0)) && (
                <button
                  type="button"
                  className={`qm-order-btn ${isConversationLogLoading ? 'loading' : ''}`}
                  onClick={handleRequestConversationLog}
                  disabled={isConversationLogLoading}
                  aria-label="대화 요약 다시 생성"
                  title="대화 요약 다시 생성"
                >
                  <span className="material-symbols-outlined">refresh</span>
                </button>
              )}
              {activeTab === 'summary' && (isViewingHistory || summary) && (
                <button
                  type="button"
                  className={`qm-order-btn ${isSummaryLoading ? 'loading' : ''}`}
                  onClick={handleRequestSummary}
                  disabled={isSummaryLoading}
                  aria-label="요약 다시 생성"
                  title="요약 다시 생성"
                >
                  <span className="material-symbols-outlined">refresh</span>
                </button>
              )}
              <button
                type="button"
                className="qm-copy-btn"
                onClick={onCopyNotes}
                disabled={!canCopy}
                aria-label="노트 복사"
                title="노트 복사"
              >
                <img src={copyIcon} alt="" />
              </button>
            </div>
          </div>
        </div>
        <div className={`qm-panel-body${isRightPanelOpen ? '' : ' panel-collapsed'}`}>
          <div className="qm-panel-main">
            {activeTab === 'script' && (
              <QuickMeetingTranscript
                variant="script"
                segments={segments}
                correctedSentences={correctedSentences}
                partialText={partialText}
                partialSpeaker={partialSpeaker}
                reverse={isReverseScript}
                prepData={prepData}
              />
            )}
            {activeTab === 'conversation' && (
              <div className="qm-summary-view">
                {conversationLog && conversationLogMarkdown && !isConversationLogLoading ? (
                  <div className="md-content">
                    <Markdown>{conversationLogMarkdown}</Markdown>
                  </div>
                ) : renderConversationLogEmptyState()}
              </div>
            )}
            {activeTab === 'summary' && (
              <div className="qm-summary-view">
                {summary && summaryMarkdown && !isSummaryLoading ? (
                  <div className="md-content">
                    <Markdown>{summaryMarkdown}</Markdown>
                  </div>
                ) : renderSummaryEmptyState()}
              </div>
            )}
          </div>
          <div className={`qm-right-panel-wrapper ${isRightPanelOpen ? 'open' : 'closed'}`}>
            <button
              type="button"
              className={`qm-panel-toggle-btn ${isRightPanelOpen ? 'open' : 'closed'}`}
              onClick={onToggleRightPanel}
              title={isRightPanelOpen ? '패널 닫기' : '패널 열기'}
            >
              <span className="material-symbols-outlined">
                {isRightPanelOpen ? 'chevron_right' : 'chevron_left'}
              </span>
            </button>
            <div className={`qm-right-panel ${isRightPanelOpen ? 'open' : 'closed'}`}>
              <div className="qm-right-panel-body">
                <div className="qm-right-panel-content">
                  {rightPanelContent}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default MeetingTabbedPanel;
