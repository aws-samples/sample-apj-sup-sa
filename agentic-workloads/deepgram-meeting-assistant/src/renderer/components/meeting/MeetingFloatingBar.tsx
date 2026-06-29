import { useState, useRef, useEffect } from 'react';
import type { RecordingState, TranscribeLanguage } from '@shared/types';
import type { Vocabulary } from '@shared/types/vocabulary';
import { SUPPORTED_LANGUAGES } from '@shared/types/settings';
import MicrophoneControl from '../MicrophoneControl';

interface MeetingFloatingBarProps {
  recordingState: RecordingState;
  displayLanguage?: TranscribeLanguage;
  selectedDeviceId: string | null;
  isMicMuted: boolean;
  vocabularies: Vocabulary[];
  onToggleMute: () => void;
  onDeviceChange: (deviceId: string | null) => void;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onOpenSummary: () => void;
  onLanguageChange?: (language: TranscribeLanguage) => void;
  onTargetLanguageChange?: (language: TranscribeLanguage) => void;
  onVocabularyChange?: (vocabularyId: string | null) => void;
}

function MeetingFloatingBar({
  recordingState,
  displayLanguage,
  selectedDeviceId,
  isMicMuted,
  vocabularies,
  onToggleMute,
  onDeviceChange,
  onStart,
  onPause,
  onResume,
  onStop,
  onOpenSummary,
  onLanguageChange,
  onTargetLanguageChange,
  onVocabularyChange,
}: MeetingFloatingBarProps) {
  const [isLangDropdownOpen, setIsLangDropdownOpen] = useState(false);
  const [isTargetLangDropdownOpen, setIsTargetLangDropdownOpen] = useState(false);
  const [isVocabDropdownOpen, setIsVocabDropdownOpen] = useState(false);
  const langDropdownRef = useRef<HTMLDivElement>(null);
  const targetLangDropdownRef = useRef<HTMLDivElement>(null);
  const vocabDropdownRef = useRef<HTMLDivElement>(null);

  const isRecording = recordingState.status === 'recording';
  const isPaused = recordingState.status === 'paused';
  const isIdle = recordingState.status === 'idle';
  const isProcessing = recordingState.status === 'processing';
  const isCompleted = recordingState.status === 'completed';

  // Helper function to get short language label
  const getShortLangLabel = (langCode: TranscribeLanguage): string => {
    switch (langCode) {
      case 'en-US': return 'EN';
      case 'ko-KR': return 'KO';
      case 'ja-JP': return 'JP';
      case 'zh-CN': return 'CN';
      default: return langCode;
    }
  };

  const getDisplayLangLabel = (langCode: TranscribeLanguage): string => {
    switch (langCode) {
      case 'en-US': return '영어';
      case 'ko-KR': return '한국어';
      case 'ja-JP': return '日本語';
      case 'zh-CN': return '中文';
      default: return langCode;
    }
  };

  const currentLang = SUPPORTED_LANGUAGES.find((l) => l.value === recordingState.language);
  const languageLabel = getDisplayLangLabel(recordingState.language);
  const targetLang = SUPPORTED_LANGUAGES.find((l) => l.value === recordingState.targetLanguage);
  const targetLanguageLabel = getDisplayLangLabel(recordingState.targetLanguage);
  const isMainActionDisabled = isProcessing || isCompleted || (isIdle && !selectedDeviceId);
  const canChangeLanguage = isIdle && onLanguageChange;
  const canChangeTargetLanguage = isIdle && onTargetLanguageChange;
  const canChangeVocabulary = isIdle && onVocabularyChange;

  // 현재 언어에 맞는 용어집만 필터링 (READY 상태만)
  const availableVocabularies = vocabularies.filter(
    (v) => v.languageCode === recordingState.language && v.awsStatus === 'READY'
  );
  const selectedVocabulary = vocabularies.find((v) => v.id === recordingState.vocabularyId);
  const vocabularyLabel = selectedVocabulary?.name ?? '용어집 없음';

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (langDropdownRef.current && !langDropdownRef.current.contains(e.target as Node)) {
        setIsLangDropdownOpen(false);
      }
      if (targetLangDropdownRef.current && !targetLangDropdownRef.current.contains(e.target as Node)) {
        setIsTargetLangDropdownOpen(false);
      }
      if (vocabDropdownRef.current && !vocabDropdownRef.current.contains(e.target as Node)) {
        setIsVocabDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleLanguageSelect = (lang: TranscribeLanguage) => {
    onLanguageChange?.(lang);
    setIsLangDropdownOpen(false);
  };

  const handleTargetLanguageSelect = (lang: TranscribeLanguage) => {
    onTargetLanguageChange?.(lang);
    setIsTargetLangDropdownOpen(false);
  };

  const handleVocabularySelect = (vocabularyId: string | null) => {
    onVocabularyChange?.(vocabularyId);
    setIsVocabDropdownOpen(false);
  };

  const handleMainAction = () => {
    if (isMainActionDisabled) {
      return;
    }
    if (isIdle) {
      onStart();
      return;
    }
    if (isRecording) {
      onPause();
      return;
    }
    if (isPaused) {
      onResume();
    }
  };

  const mainActionLabel = isIdle
    ? '녹음 시작'
    : isRecording
      ? '일시 정지'
      : isPaused
        ? '재개'
        : '녹음 완료됨';

  return (
    <div className="qm-floating">
      <div className="qm-floating-bar">
        <div className="qm-floating-controls">
          <MicrophoneControl
            onDeviceChange={onDeviceChange}
            isMuted={isMicMuted}
            onToggleMute={onToggleMute}
          />
          <div className={`qm-waveform ${isRecording ? 'active' : ''}`}>
            <span />
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
          <span className="qm-floating-divider" />
          <div className="qm-dropdown-wrapper" ref={langDropdownRef}>
            <button
              type="button"
              className="qm-pill-control"
              disabled={!canChangeLanguage}
              onClick={() => canChangeLanguage && setIsLangDropdownOpen(!isLangDropdownOpen)}
              title={canChangeLanguage ? '인식 언어 선택' : '녹음 중에는 변경 불가'}
            >
              <span className="material-symbols-outlined">headset</span>
              <span>{languageLabel}</span>
              <span className="material-symbols-outlined">expand_more</span>
            </button>
            {isLangDropdownOpen && (
              <div className="qm-dropdown-menu">
                {SUPPORTED_LANGUAGES.map((lang) => (
                  <button
                    key={lang.value}
                    type="button"
                    className={`qm-dropdown-item ${recordingState.language === lang.value ? 'active' : ''}`}
                    onClick={() => handleLanguageSelect(lang.value)}
                  >
                    <span>{lang.icon}</span>
                    <span>{getDisplayLangLabel(lang.value)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="qm-dropdown-wrapper" ref={vocabDropdownRef}>
            <button
              type="button"
              className="qm-pill-control"
              disabled={!canChangeVocabulary}
              onClick={() => canChangeVocabulary && setIsVocabDropdownOpen(!isVocabDropdownOpen)}
              title={canChangeVocabulary ? '용어집 선택' : '녹음 중에는 변경 불가'}
            >
              <span className="material-symbols-outlined">dictionary</span>
              <span>{vocabularyLabel}</span>
              <span className="material-symbols-outlined">expand_more</span>
            </button>
            {isVocabDropdownOpen && (
              <div className="qm-dropdown-menu">
                <button
                  type="button"
                  className={`qm-dropdown-item ${!recordingState.vocabularyId ? 'active' : ''}`}
                  onClick={() => handleVocabularySelect(null)}
                >
                  <span className="material-symbols-outlined">block</span>
                  <span>용어집 없음</span>
                </button>
                {availableVocabularies.map((vocab) => (
                  <button
                    key={vocab.id}
                    type="button"
                    className={`qm-dropdown-item ${recordingState.vocabularyId === vocab.id ? 'active' : ''}`}
                    onClick={() => handleVocabularySelect(vocab.id)}
                  >
                    <span className="material-symbols-outlined">
                      {vocab.isBuiltin ? 'auto_stories' : 'book'}
                    </span>
                    <span>{vocab.name}</span>
                  </button>
                ))}
                {availableVocabularies.length === 0 && (
                  <div className="qm-dropdown-empty">
                    동기화된 용어집 없음
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="qm-dropdown-wrapper" ref={targetLangDropdownRef}>
            <button
              type="button"
              className="qm-pill-control"
              disabled={!canChangeTargetLanguage}
              onClick={() => canChangeTargetLanguage && setIsTargetLangDropdownOpen(!isTargetLangDropdownOpen)}
              title={canChangeTargetLanguage ? '번역 언어 선택' : '녹음 중에는 변경 불가'}
            >
              <span className="material-symbols-outlined">translate</span>
              <span>{targetLanguageLabel}</span>
              <span className="material-symbols-outlined">expand_more</span>
            </button>
            {isTargetLangDropdownOpen && (
              <div className="qm-dropdown-menu">
                {SUPPORTED_LANGUAGES.map((lang) => (
                  <button
                    key={lang.value}
                    type="button"
                    className={`qm-dropdown-item ${recordingState.targetLanguage === lang.value ? 'active' : ''}`}
                    onClick={() => handleTargetLanguageSelect(lang.value)}
                  >
                    <span>{lang.icon}</span>
                    <span>{getDisplayLangLabel(lang.value)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <button
          type="button"
          className={`qm-action-btn ${isRecording ? 'pause' : isPaused ? 'resume' : 'record'}`}
          onClick={handleMainAction}
          disabled={isMainActionDisabled}
          aria-label={mainActionLabel}
          title={mainActionLabel}
        >
          {isRecording ? (
            <span className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>
              pause
            </span>
          ) : isPaused ? (
            <span className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>
              play_arrow
            </span>
          ) : (
            <span className="qm-rec-dot" />
          )}
        </button>
        {(isRecording || isPaused) && (
          <button
            type="button"
            className="qm-stop-pill"
            onClick={onStop}
            disabled={isProcessing}
            aria-label="기록 종료"
            title="기록 종료"
          >
            기록 종료
          </button>
        )}
        <button
          type="button"
          className="qm-notes-btn"
          onClick={onOpenSummary}
          aria-label="AI 회의록 보기"
          title="AI 회의록 보기"
        >
          <span className="material-symbols-outlined">notes</span>
        </button>
      </div>
    </div>
  );
}

export default MeetingFloatingBar;
