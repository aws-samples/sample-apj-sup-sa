import { useState, useEffect, useCallback, useRef } from 'react';
import MeetingWorkspace from '../meeting/MeetingWorkspace';
import type { MeetingWorkspaceProps } from '../meeting/MeetingWorkspace';
import type { LeadershipPrinciple, InterviewSuggestionItem } from '../../../shared/types/interview';
import { LEADERSHIP_PRINCIPLES, LP_QUESTIONS, getLPInfo } from '../../../shared/constants/interview-questions';

function InterviewMeetingView(props: MeetingWorkspaceProps) {
  const meetingId = props.recordingState.meetingId;
  const correctedCount = props.correctedSentences.length;
  const hasEnoughContext = correctedCount >= 2;

  const [selectedLPs, setSelectedLPs] = useState<LeadershipPrinciple[]>([]);
  const [suggestions, setSuggestions] = useState<InterviewSuggestionItem[]>([]);
  const [isSuggestionsLoading, setIsSuggestionsLoading] = useState(false);
  const lastCorrectionCountRef = useRef(0);
  const pendingSuggestionCountRef = useRef(0);
  const isSuggestionsLoadingRef = useRef(isSuggestionsLoading);

  useEffect(() => {
    isSuggestionsLoadingRef.current = isSuggestionsLoading;
  }, [isSuggestionsLoading]);

  useEffect(() => {
    setSuggestions([]);
    setIsSuggestionsLoading(false);
    isSuggestionsLoadingRef.current = false;
    lastCorrectionCountRef.current = 0;
    pendingSuggestionCountRef.current = 0;
  }, [meetingId]);

  const handleLPChange = (index: number, value: string) => {
    const lpId = value as LeadershipPrinciple;
    setSelectedLPs((prev) => {
      const newLPs = [...prev];
      if (value === '') {
        newLPs.splice(index, 1);
      } else if (index < prev.length) {
        newLPs[index] = lpId;
      } else {
        newLPs.push(lpId);
      }
      return newLPs;
    });
  };

  const getAvailableLPs = (currentIndex: number) => {
    const otherSelected = selectedLPs.filter((_, i) => i !== currentIndex);
    return LEADERSHIP_PRINCIPLES.filter((lp) => !otherSelected.includes(lp.id));
  };

  const requestSuggestions = useCallback(async (count: number) => {
    if (isSuggestionsLoadingRef.current || selectedLPs.length === 0) return;
    if (count <= 0 || !meetingId || !window.electronAPI) {
      pendingSuggestionCountRef.current += count;
      return;
    }

    isSuggestionsLoadingRef.current = true;
    setIsSuggestionsLoading(true);
    const result = await window.electronAPI.generateInterviewSuggestions({
      meetingId,
      lpIds: selectedLPs,
      count,
    });
    if (result.success && result.suggestions) {
      setSuggestions((prev) => [...result.suggestions!.suggestions, ...prev]);
    } else if (props.onError) {
      props.onError(result.error || 'AI 제안 생성 실패');
    }
    isSuggestionsLoadingRef.current = false;
    setIsSuggestionsLoading(false);
  }, [meetingId, selectedLPs, props.onError]);

  const flushSuggestionQueue = useCallback(() => {
    if (isSuggestionsLoadingRef.current || selectedLPs.length === 0) return;
    const pendingCount = pendingSuggestionCountRef.current;
    if (pendingCount <= 0) return;
    const batchCount = Math.min(pendingCount, 10);
    pendingSuggestionCountRef.current = pendingCount - batchCount;
    requestSuggestions(batchCount);
  }, [requestSuggestions, selectedLPs.length]);

  useEffect(() => {
    if (!meetingId || !hasEnoughContext || selectedLPs.length === 0) return;

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
  }, [meetingId, hasEnoughContext, correctedCount, selectedLPs.length, flushSuggestionQueue]);

  useEffect(() => {
    if (!isSuggestionsLoading) flushSuggestionQueue();
  }, [isSuggestionsLoading, flushSuggestionQueue]);

  const selectedQuestions = selectedLPs.flatMap((lpId) =>
    (LP_QUESTIONS[lpId] || []).map((q) => ({ ...q, lpInfo: getLPInfo(lpId) }))
  );

  const lpSelectionPanel = (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header">
        <h4>Leadership Principles</h4>
      </div>
      <div className="qm-ai-panel-body">
        <div className="interview-lp-selectors">
          {[0, 1].map((index) => (
            <select
              key={index}
              className="interview-lp-select"
              value={selectedLPs[index] || ''}
              onChange={(e) => handleLPChange(index, e.target.value)}
            >
              <option value="">LP {index + 1} 선택</option>
              {getAvailableLPs(index).map((lp) => (
                <option key={lp.id} value={lp.id}>
                  {lp.name}
                </option>
              ))}
            </select>
          ))}
        </div>
      </div>
    </div>
  );

  const questionsPanel = selectedLPs.length > 0 && (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header">
        <h4>LP Questions</h4>
        <p>{selectedQuestions.length}개 질문</p>
      </div>
      <div className="qm-ai-panel-body">
        <div className="interview-questions-scroll">
          <ol className="interview-question-list">
            {selectedQuestions.map((q, idx) => (
              <li key={q.id} className="interview-question-item">
                <span className="interview-question-num">{idx + 1}</span>
                <div className="interview-question-content">
                  <span className="interview-question-lp-tag">{q.lpInfo?.shortName}</span>
                  <span className="interview-question-text">{q.text}</span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );

  const aiSuggestionsPanel = (
    <div className="qm-right-panel-card qm-ai-suggestions-card">
      <div className="qm-right-panel-card-header">
        <h4>AI Follow-up Questions</h4>
      </div>
      <div className="qm-ai-panel-body qm-ai-panel-body--fill">
        {suggestions.length === 0 ? (
          <div className="qm-empty-state qm-right-panel-empty">
            {selectedLPs.length === 0
              ? 'LP를 선택하면 후속 질문이 생성됩니다.'
              : hasEnoughContext
              ? isSuggestionsLoading
                ? 'AI 제안을 생성 중입니다.'
                : '아직 생성된 제안이 없습니다.'
              : '대화가 조금 더 필요합니다. 2문장 이후 자동으로 제안이 생성됩니다.'}
          </div>
        ) : (
          <div className="qm-ai-suggestion-scroll">
            <ul className="qm-ai-suggestion-list">
              {suggestions.map((item, index) => (
                <li key={`suggestion-${index}`} className="qm-ai-suggestion-item">
                  <span className="interview-suggestion-lp-tag">{item.lpName}</span>
                  <p className="qm-ai-suggestion-text">{item.text}</p>
                </li>
              ))}
            </ul>
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
          {lpSelectionPanel}
          {questionsPanel}
          {aiSuggestionsPanel}
        </div>
      }
    />
  );
}

export default InterviewMeetingView;
