import { useCallback, useEffect, useRef, useState } from 'react';
import type { TranslatedSuggestionItem } from '@shared/types';
import type { MeetingWorkspaceProps } from '../meeting/MeetingWorkspace';
import MeetingWorkspace from '../meeting/MeetingWorkspace';

function TranslatedMeetingView(props: MeetingWorkspaceProps) {
  const meetingId = props.recordingState.meetingId;
  const correctedCount = props.correctedSentences.length;
  const hasEnoughContext = correctedCount >= 2;
  const [suggestions, setSuggestions] = useState<TranslatedSuggestionItem[]>([]);
  const [isSuggestionsLoading, setIsSuggestionsLoading] = useState(false);
  const [suggestionsError, setSuggestionsError] = useState<string | null>(null);
  const lastCorrectionCountRef = useRef(0);
  const pendingSuggestionCountRef = useRef(0);
  const isSuggestionsLoadingRef = useRef(isSuggestionsLoading);

  const [translationInput, setTranslationInput] = useState('');
  const [translationResult, setTranslationResult] = useState('');
  const [isTranslating, setIsTranslating] = useState(false);
  const [translationError, setTranslationError] = useState<string | null>(null);

  useEffect(() => {
    isSuggestionsLoadingRef.current = isSuggestionsLoading;
  }, [isSuggestionsLoading]);

  useEffect(() => {
    setSuggestions([]);
    setSuggestionsError(null);
    setIsSuggestionsLoading(false);
    isSuggestionsLoadingRef.current = false;
    setTranslationInput('');
    setTranslationResult('');
    setTranslationError(null);
    setIsTranslating(false);
    lastCorrectionCountRef.current = 0;
    pendingSuggestionCountRef.current = 0;
  }, [meetingId]);

  const requestSuggestions = useCallback(async (count: number) => {
    if (isSuggestionsLoadingRef.current) {
      return;
    }
    if (count <= 0) {
      return;
    }
    if (!meetingId) {
      setSuggestionsError('미팅을 시작한 뒤 제안을 생성할 수 있습니다.');
      pendingSuggestionCountRef.current += count;
      return;
    }
    if (!window.electronAPI) {
      setSuggestionsError('AI 제안은 데스크톱 앱에서만 사용할 수 있습니다.');
      pendingSuggestionCountRef.current += count;
      return;
    }

    isSuggestionsLoadingRef.current = true;
    setIsSuggestionsLoading(true);
    setSuggestionsError(null);
    const result = await window.electronAPI.generateEnglishSuggestions({ meetingId, count });
    if (!result.success) {
      setSuggestionsError(result.error || 'AI 제안 생성 실패');
      isSuggestionsLoadingRef.current = false;
      setIsSuggestionsLoading(false);
      pendingSuggestionCountRef.current += count;
      return;
    }

    const nextItems = result.suggestions?.suggestions ?? [];
    setSuggestions((prev) => [...nextItems, ...prev]);
    isSuggestionsLoadingRef.current = false;
    setIsSuggestionsLoading(false);
  }, [meetingId]);

  const flushSuggestionQueue = useCallback(() => {
    if (isSuggestionsLoadingRef.current) {
      return;
    }
    const pendingCount = pendingSuggestionCountRef.current;
    if (pendingCount <= 0) {
      return;
    }
    const batchCount = Math.min(pendingCount, 10);
    pendingSuggestionCountRef.current = pendingCount - batchCount;
    requestSuggestions(batchCount);
  }, [requestSuggestions]);

  useEffect(() => {
    if (!meetingId || !hasEnoughContext) {
      return;
    }

    if (lastCorrectionCountRef.current === 0) {
      lastCorrectionCountRef.current = correctedCount;
      pendingSuggestionCountRef.current += 3;
      flushSuggestionQueue();
      return;
    }

    if (correctedCount > lastCorrectionCountRef.current) {
      const delta = correctedCount - lastCorrectionCountRef.current;
      lastCorrectionCountRef.current = correctedCount;
      pendingSuggestionCountRef.current += delta * 2;
      flushSuggestionQueue();
    }
  }, [meetingId, hasEnoughContext, correctedCount, flushSuggestionQueue]);

  useEffect(() => {
    if (!isSuggestionsLoading) {
      flushSuggestionQueue();
    }
  }, [isSuggestionsLoading, flushSuggestionQueue]);

  const handleTranslate = useCallback(async () => {
    if (isTranslating) {
      return;
    }
    const trimmed = translationInput.trim();
    if (!trimmed) {
      setTranslationError('번역할 문장을 입력하세요.');
      return;
    }
    if (!window.electronAPI) {
      setTranslationError('번역 기능은 데스크톱 앱에서만 사용할 수 있습니다.');
      return;
    }

    setIsTranslating(true);
    setTranslationError(null);
    const result = await window.electronAPI.translateEnglishText({ meetingId, text: trimmed });
    if (!result.success) {
      setTranslationError(result.error || '번역 실패');
      setTranslationResult('');
      setIsTranslating(false);
      return;
    }

    setTranslationResult(result.translatedText ?? '');
    setIsTranslating(false);
  }, [isTranslating, meetingId, translationInput]);

  useEffect(() => {
    if (suggestionsError && props.onError) {
      props.onError(suggestionsError);
    }
  }, [suggestionsError, props.onError]);

  useEffect(() => {
    if (translationError && props.onError) {
      props.onError(translationError);
    }
  }, [translationError, props.onError]);

  const aiSuggestionsPanel = (
    <div className="qm-right-panel-card qm-ai-suggestions-card">
      <div className="qm-right-panel-card-header">
        <h4>AI Suggestions</h4>
      </div>
      <div className="qm-ai-panel-body qm-ai-panel-body--fill">
        {suggestions.length === 0 ? (
          <div className="qm-empty-state qm-right-panel-empty">
            {hasEnoughContext
              ? (isSuggestionsLoading ? 'AI 제안을 생성 중입니다.' : '아직 생성된 제안이 없습니다.')
              : '대화가 조금 더 필요합니다. 2문장 이후 자동으로 제안이 생성됩니다.'}
          </div>
        ) : (
          <>
            <div className="qm-ai-suggestion-scroll">
              <ul className="qm-ai-suggestion-list">
                {suggestions.map((item, index) => (
                  <li key={`suggestion-${index}`} className="qm-ai-suggestion-item">
                    <p className="qm-ai-suggestion-text">{item.text}</p>
                    {item.translatedText && (
                      <p className="qm-ai-suggestion-translation">{item.translatedText}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </div>
    </div>
  );

  const aiTranslationPanel = (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header">
        <h4>AI Translation</h4>
      </div>
      <div className="qm-ai-panel-body">
        <form
          className="qm-translation-form"
          onSubmit={(event) => {
            event.preventDefault();
            handleTranslate();
          }}
        >
          <textarea
            className="form-textarea qm-translation-input"
            rows={3}
            placeholder="한국어 문장을 입력하세요. (Enter로 번역)"
            value={translationInput}
            onChange={(event) => setTranslationInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                handleTranslate();
              }
            }}
          />
          <div className="qm-translation-actions">
            <button
              type="submit"
              className="qm-right-panel-action"
              disabled={isTranslating || !translationInput.trim()}
            >
              {isTranslating ? '번역 중...' : '번역'}
            </button>
          </div>
        </form>
        {translationResult && (
          <div className="qm-translation-result">
            <span className="qm-translation-label">Suggested English</span>
            <p>{translationResult}</p>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <MeetingWorkspace
      {...props}
      rightPanelContent={
        <div className="qm-right-panel-stack">
          {aiTranslationPanel}
          {aiSuggestionsPanel}
        </div>
      }
    />
  );
}

export default TranslatedMeetingView;
