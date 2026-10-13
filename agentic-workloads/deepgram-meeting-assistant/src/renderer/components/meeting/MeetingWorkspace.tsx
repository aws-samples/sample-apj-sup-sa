import { type ReactNode, useState } from 'react';
import type { TranscribeLanguage, MeetingPrepData, ConversationLog } from '@shared/types';
import type { Vocabulary } from '@shared/types/vocabulary';
import MeetingFloatingBar from './MeetingFloatingBar';
import MeetingTabbedPanel from './MeetingTabbedPanel';
import type { BaseMeetingViewProps, QuickMeetingTab } from '../meeting-types/types';

export interface MeetingWorkspaceProps extends BaseMeetingViewProps {
  activeTab: QuickMeetingTab;
  onTabChange: (tab: QuickMeetingTab) => void;
  onCopyNotes: () => void;
  isReverseScript: boolean;
  onToggleReverseScript: () => void;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  selectedDeviceId: string | null;
  isMicMuted: boolean;
  onToggleMute: () => void;
  onDeviceChange: (deviceId: string | null) => void;
  meetingTitle: string;
  onTitleChange: (title: string) => void;
  onLanguageChange?: (language: TranscribeLanguage) => void;
  onTargetLanguageChange?: (language: TranscribeLanguage) => void;
  displayLanguage?: TranscribeLanguage;
  meetingLabel: string;
  onBack: () => void;
  rightPanelContent?: ReactNode;
  /** 미팅 준비 데이터 (Requirements: 7.2, 8.1) */
  prepData?: MeetingPrepData | null;
  /** 미팅 준비 데이터 변경 콜백 */
  onPrepDataChange?: (prepData: MeetingPrepData | null) => void;
  isViewingHistory?: boolean;
  isSummaryLoading?: boolean;
  onRequestSummary?: (meetingId: string) => Promise<unknown>;
  /** 대화 로그 */
  conversationLog?: ConversationLog | null;
  isConversationLogLoading?: boolean;
  conversationLogError?: string | null;
  onRequestConversationLog?: (meetingId: string) => Promise<unknown>;
  onError?: (message: string | null) => void;
  /** 용어집 목록 */
  vocabularies?: Vocabulary[];
  /** 용어집 변경 콜백 */
  onVocabularyChange?: (vocabularyId: string | null) => void;
}

function MeetingWorkspace({
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
  isSummaryLoading,
  activeTab,
  onTabChange,
  onCopyNotes,
  isReverseScript,
  onToggleReverseScript,
  onStart,
  onPause,
  onResume,
  onStop,
  selectedDeviceId,
  isMicMuted,
  onToggleMute,
  onDeviceChange,
  meetingTitle,
  onTitleChange,
  onLanguageChange,
  onTargetLanguageChange,
  displayLanguage,
  meetingLabel,
  onBack,
  rightPanelContent,
  prepData,
  isViewingHistory = false,
  onRequestSummary,
  conversationLog,
  isConversationLogLoading,
  conversationLogError,
  onRequestConversationLog,
  onError,
  vocabularies = [],
  onVocabularyChange,
}: MeetingWorkspaceProps) {
  const [isRightPanelOpen, setIsRightPanelOpen] = useState(true);

  const handleOpenSummary = () => {
    onTabChange('summary');
  };

  const panelContent = rightPanelContent || (
    <div className="qm-placeholder-panel">
      <p>추가 정보를 준비 중입니다.</p>
    </div>
  );

  return (
    <div className="quick-meeting-wrapper qm-view">
      <MeetingTabbedPanel
        recordingState={recordingState}
        segments={segments}
        correctedSentences={correctedSentences}
        partialText={partialText}
        partialSpeaker={partialSpeaker}
        summary={summary}
        fullScript={fullScript}
        transcriptionError={transcriptionError}
        audioError={audioError}
        summaryError={summaryError}
        isSummaryLoading={isSummaryLoading}
        activeTab={activeTab}
        onTabChange={onTabChange}
        onCopyNotes={onCopyNotes}
        isReverseScript={isReverseScript}
        onToggleReverseScript={onToggleReverseScript}
        meetingTitle={meetingTitle}
        onTitleChange={onTitleChange}
        meetingLabel={meetingLabel}
        onBack={onBack}
        rightPanelContent={panelContent}
        isRightPanelOpen={isRightPanelOpen}
        onToggleRightPanel={() => setIsRightPanelOpen((prev) => !prev)}
        prepData={prepData}
        isViewingHistory={isViewingHistory}
        onRequestSummary={onRequestSummary}
        conversationLog={conversationLog}
        isConversationLogLoading={isConversationLogLoading}
        conversationLogError={conversationLogError}
        onRequestConversationLog={onRequestConversationLog}
      />
      {!isViewingHistory && (
        <MeetingFloatingBar
          recordingState={recordingState}
          displayLanguage={displayLanguage}
          selectedDeviceId={selectedDeviceId}
          isMicMuted={isMicMuted}
          vocabularies={vocabularies}
          onToggleMute={onToggleMute}
          onDeviceChange={onDeviceChange}
          onStart={onStart}
          onPause={onPause}
          onResume={onResume}
          onStop={onStop}
          onOpenSummary={handleOpenSummary}
          onLanguageChange={onLanguageChange}
          onTargetLanguageChange={onTargetLanguageChange}
          onVocabularyChange={onVocabularyChange}
        />
      )}
    </div>
  );
}

export default MeetingWorkspace;
