import { useAppState } from './hooks/useAppState';
import Sidebar from './components/Sidebar';
import MeetingTypeSelector from './components/MeetingTypeSelector';
import MeetingView from './components/MeetingView';
import Settings from './components/Settings';
import ErrorBanner from './components/ErrorBanner';

function App() {
  const {
    activeNav,
    selectedMeetingType,
    selectedMeetingId,
    selectedMeetingDetail,
    selectedDeviceId,
    isMicMuted,
    historyError,
    meetingError,
    appError,
    meetings,
    isHistoryLoading,
    hasMore,
    recordingState,
    vocabularies,
    setActiveNav,
    loadMore,
    handleMeetingTypeSelect,
    handleBackToSelection,
    handleMeetingSelect,
    handleMeetingDelete,
    handleStartRecording,
    handlePauseRecording,
    handleResumeRecording,
    handleEndRecording,
    handleDeviceChange,
    handleLanguageChange,
    handleTargetLanguageChange,
    handleVocabularyChange,
    toggleMute,
    setRecordingState,
    setAppError,
    clearError,
  } = useAppState();

  const renderContent = () => {
    if (activeNav === 'home') {
      if (!selectedMeetingType) {
        return <MeetingTypeSelector onSelect={handleMeetingTypeSelect} />;
      }
      return (
        <MeetingView
          meetingType={selectedMeetingType}
          recordingState={recordingState}
          setRecordingState={setRecordingState}
          onBack={handleBackToSelection}
          selectedDeviceId={selectedDeviceId}
          isMicMuted={isMicMuted}
          onDeviceChange={handleDeviceChange}
          onToggleMute={toggleMute}
          onStartRecording={handleStartRecording}
          onPauseRecording={handlePauseRecording}
          onResumeRecording={handleResumeRecording}
          onEndRecording={handleEndRecording}
          onLanguageChange={handleLanguageChange}
          onTargetLanguageChange={handleTargetLanguageChange}
          vocabularies={vocabularies}
          onVocabularyChange={handleVocabularyChange}
          meetingDetail={selectedMeetingDetail}
          onError={setAppError}
        />
      );
    }

    if (activeNav === 'settings') {
      return <Settings />;
    }

    return null;
  };

  const displayedError = historyError || meetingError || appError;

  return (
    <div className="app">
      <div className="window-bar" />
      <ErrorBanner message={displayedError || ''} onClear={clearError} />
      <Sidebar
        activeNav={activeNav}
        onNavChange={setActiveNav}
        meetings={meetings}
        selectedMeetingId={selectedMeetingId}
        onMeetingSelect={handleMeetingSelect}
        onMeetingDelete={handleMeetingDelete}
        isHistoryLoading={isHistoryLoading}
        hasMore={hasMore}
        onLoadMore={loadMore}
      />

      <main className="main-content">
        <div className="content-area">
          {renderContent()}
        </div>
      </main>
    </div>
  );
}

export default App;
