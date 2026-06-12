import { useState, useRef, useEffect, useMemo } from 'react';
import type { CorrectedSentence, TranscriptionSegment } from '@shared/types';
import type { MeetingPrepData } from '@shared/types/meeting-prep';
import { buildMergedTranscriptItems } from '../utils/transcript-format';
import { formatMeetingPrepAsSegment, isMeetingPrepDataValid } from '../utils/meeting-prep-format';

export type QuickMeetingTranscriptVariant = 'conversation' | 'script';

export interface TranscriptItem {
  id: string;
  speakerLabel: string | null;
  text: string;
  translatedText?: string | null;
  startTime: number;
  isCorrected: boolean;
}

interface TranscriptBlock {
  speakerLabel: string | null;
  startTime: number;
  lines: Array<{ id: string; text: string; translatedText?: string | null; isCorrected: boolean }>;
}

interface QuickMeetingTranscriptProps {
  variant: QuickMeetingTranscriptVariant;
  segments: TranscriptionSegment[];
  correctedSentences: CorrectedSentence[];
  partialText: string;
  partialSpeaker: string | null;
  reverse: boolean;
  /** 미팅 준비 데이터 (Requirements: 8.1) */
  prepData?: MeetingPrepData | null;
}

interface TextPopup {
  visible: boolean;
  x: number;
  y: number;
  text: string;
  blockIndex: number;
  lineIndex: number;
}

const formatTimestamp = (seconds: number): string => {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const hrs = Math.floor(totalSeconds / 3600);
  const mins = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;

  if (hrs > 0) {
    return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
};

// 같은 화자(또는 화자 라벨이 없는 모드)라도 이 간격(초) 이상 벌어지면 새 블록으로
// 분리해 각 블록이 자기 startTime을 표시하게 한다. agentic 모드는 speaker
// diarization이 없어 speakerLabel이 전부 null이므로, 이 시간 기반 분리가 없으면
// 전체가 한 블록으로 묶여 timestamp가 00:00에 고정된다.
export const BLOCK_GAP_SEC = 8;

// export: 시간 간격 기반 블록 분리 로직의 단위 테스트를 위해 노출.
export const buildBlocks = (items: TranscriptItem[]): TranscriptBlock[] => {
  const blocks: TranscriptBlock[] = [];
  for (const item of items) {
    const lastBlock = blocks[blocks.length - 1];
    const speakerChanged = !lastBlock || lastBlock.speakerLabel !== item.speakerLabel;
    // lastBlock.startTime은 그 블록의 시작 시각이다. 블록이 길어져도 시작 기준으로
    // 간격을 재면 한 블록이 무한정 커지는 걸 막고 일정 시간마다 새 timestamp가 찍힌다.
    const gapExceeded =
      lastBlock !== undefined && item.startTime - lastBlock.startTime >= BLOCK_GAP_SEC;
    if (speakerChanged || gapExceeded) {
      blocks.push({
        speakerLabel: item.speakerLabel,
        startTime: item.startTime,
        lines: [{ id: item.id, text: item.text, translatedText: item.translatedText, isCorrected: item.isCorrected }],
      });
    } else {
      lastBlock.lines.push({ id: item.id, text: item.text, translatedText: item.translatedText, isCorrected: item.isCorrected });
    }
  }
  return blocks;
};

// "진행 중(pending, italic)" 라인 1개의 id를 계산한다.
// italic은 "교정 대기"가 아니라 "지금 말하는 중"을 의미해야 한다. 따라서
//  - partialText가 있으면(아직 말하는 중) → 확정된 라인은 모두 non-italic.
//    진행중 표시는 별도 partial 블록이 담당하므로 pending 라인은 없음(null).
//  - partialText가 없으면(직전 발화가 끝남) → 가장 최근(startTime 최대) 1개 라인만
//    pending. items는 startTime 오름차순이라 마지막 원소가 최신이다.
// id로 매칭하므로 reverse 모드에서 위치가 뒤집혀도 정확히 같은 라인이 잡힌다.
export const computePendingLineId = (
  items: TranscriptItem[],
  partialText: string
): string | null => {
  if (partialText) return null;
  if (items.length === 0) return null;
  return items[items.length - 1].id;
};

const SPEAKER_COLORS = ['#6366f1', '#06b6d4', '#f97316', '#22c55e', '#a855f7'];

const getSpeakerColor = (label: string | null): string => {
  if (!label) {
    return SPEAKER_COLORS[0];
  }
  let hash = 0;
  for (let i = 0; i < label.length; i++) {
    hash = (hash + label.charCodeAt(i)) % SPEAKER_COLORS.length;
  }
  return SPEAKER_COLORS[hash];
};

const sanitizeStrongMarkup = (value: string): string => {
  const openToken = '__STRONG_OPEN__';
  const closeToken = '__STRONG_CLOSE__';
  const normalized = value
    .replace(/<strong>/gi, openToken)
    .replace(/<\/strong>/gi, closeToken);
  const escaped = normalized
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return escaped
    .replace(new RegExp(openToken, 'g'), '<strong>')
    .replace(new RegExp(closeToken, 'g'), '</strong>');
};

function QuickMeetingTranscript({
  variant,
  segments,
  correctedSentences,
  partialText,
  partialSpeaker,
  reverse,
  prepData,
}: QuickMeetingTranscriptProps) {
  const [popup, setPopup] = useState<TextPopup>({
    visible: false,
    x: 0,
    y: 0,
    text: '',
    blockIndex: -1,
    lineIndex: -1,
  });
  const transcriptRef = useRef<HTMLDivElement>(null);

  // 팝업 외부 클릭 시 닫기
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (popup.visible && transcriptRef.current && !transcriptRef.current.contains(event.target as Node)) {
        setPopup(prev => ({ ...prev, visible: false }));
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [popup.visible]);

  const handleTextClick = (event: React.MouseEvent, text: string, blockIndex: number, lineIndex: number) => {
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    const transcriptRect = transcriptRef.current?.getBoundingClientRect();
    
    if (transcriptRect) {
      setPopup({
        visible: true,
        x: rect.left - transcriptRect.left + rect.width / 2,
        y: rect.top - transcriptRect.top - 10,
        text,
        blockIndex,
        lineIndex,
      });
    }
  };

  const handlePopupAction = (action: string) => {
    console.log(`Action: ${action}, Text: ${popup.text}`);
    // 여기에 각 액션에 대한 로직을 추가할 수 있습니다
    setPopup(prev => ({ ...prev, visible: false }));
  };
  const items = useMemo<TranscriptItem[]>(() => {
    if (variant === 'script') {
      return buildMergedTranscriptItems(segments, correctedSentences);
    }

    if (segments.length > 0) {
      return segments.map((segment) => ({
        id: segment.id,
        speakerLabel: segment.speakerLabel,
        text: segment.text,
        startTime: segment.startTime,
        isCorrected: false,
      }));
    }

    return correctedSentences.map((sentence) => ({
      id: sentence.id,
      speakerLabel: sentence.speakerLabel,
      text: sentence.correctedText,
      translatedText: sentence.translatedText ?? null,
      startTime: sentence.startTime,
      isCorrected: true,
    }));
  }, [variant, segments, correctedSentences]);

  const showTranslation = variant === 'script';
  const orderedBlocks = useMemo(() => {
    const orderedItems = reverse ? [...items].reverse() : items;
    return buildBlocks(orderedItems);
  }, [items, reverse]);

  const pendingLineId = computePendingLineId(items, partialText);
  const isEmpty = orderedBlocks.length === 0 && !partialText;

  // 미팅 준비 세그먼트 텍스트 생성 (Requirements: 8.1)
  const meetingPrepSegmentText = prepData && isMeetingPrepDataValid(prepData)
    ? formatMeetingPrepAsSegment(prepData)
    : null;

  return (
    <div className="qm-transcript" ref={transcriptRef}>
      {/* 텍스트 팝업 */}
      {popup.visible && (
        <div 
          className="text-popup"
          style={{
            position: 'absolute',
            left: popup.x,
            top: popup.y,
            transform: 'translateX(-50%)',
            zIndex: 1000,
          }}
        >
          <div className="text-popup-content">
            <button 
              className="text-popup-btn"
              onClick={() => handlePopupAction('copy')}
              title="복사"
            >
              <span className="material-symbols-outlined">content_copy</span>
            </button>
            <button 
              className="text-popup-btn"
              onClick={() => handlePopupAction('highlight')}
              title="하이라이트"
            >
              <span className="material-symbols-outlined">highlight</span>
            </button>
            <button 
              className="text-popup-btn"
              onClick={() => handlePopupAction('note')}
              title="메모 추가"
            >
              <span className="material-symbols-outlined">note_add</span>
            </button>
          </div>
        </div>
      )}

      {isEmpty && !meetingPrepSegmentText && (
        <div className="qm-empty-state">대화 내용이 없습니다.</div>
      )}
      
      {/* 미팅 준비 세그먼트 - 역순이 아닌 경우 최상단에 표시 (Requirements: 8.1) */}
      {!reverse && meetingPrepSegmentText && (
        <div className="qm-script-block qm-meeting-prep-segment">
          <div className="qm-script-meta">
            <span
              className="qm-speaker-dot"
              style={{ backgroundColor: '#10b981' }}
              aria-hidden="true"
            />
            <span className="qm-timestamp">미팅 준비</span>
          </div>
          <div className="qm-script-lines">
            <div className="qm-script-line">
              <pre className="qm-script-text qm-meeting-prep-text">{meetingPrepSegmentText}</pre>
            </div>
          </div>
        </div>
      )}
      
      {reverse && partialText && (
        <div className="qm-partial-block">
          <div className="qm-partial-content">
            {partialSpeaker && (
              <span className="qm-partial-label">{partialSpeaker}</span>
            )}
            <p className="qm-partial-text">{partialText}</p>
          </div>
        </div>
      )}
      {orderedBlocks.map((block, blockIndex) => (
        <div key={`${block.startTime}-${blockIndex}`} className="qm-script-block">
          <div className="qm-script-meta">
            <span
              className="qm-speaker-dot"
              style={{ backgroundColor: getSpeakerColor(block.speakerLabel) }}
              aria-hidden="true"
            />
            <span className="qm-timestamp">{formatTimestamp(block.startTime)}</span>
          </div>
          <div className="qm-script-lines">
            {block.lines.map((line, lineIndex) => (
              <div key={`${block.startTime}-${lineIndex}`} className="qm-script-line">
                <p className={`qm-script-text${line.id === pendingLineId ? ' is-pending' : ''}`}>{line.text}</p>
                {showTranslation && line.translatedText && (
                  <p
                    className="qm-script-translation"
                    dangerouslySetInnerHTML={{ __html: sanitizeStrongMarkup(line.translatedText) }}
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
      {!reverse && partialText && (
        <div className="qm-partial-block">
          <div className="qm-partial-content">
            {partialSpeaker && (
              <span className="qm-partial-label">{partialSpeaker}</span>
            )}
            <p className="qm-partial-text">{partialText}</p>
          </div>
        </div>
      )}
      
      {/* 미팅 준비 세그먼트 - 역순인 경우 최하단에 표시 (Requirements: 8.1) */}
      {reverse && meetingPrepSegmentText && (
        <div className="qm-script-block qm-meeting-prep-segment">
          <div className="qm-script-meta">
            <span
              className="qm-speaker-dot"
              style={{ backgroundColor: '#10b981' }}
              aria-hidden="true"
            />
            <span className="qm-timestamp">미팅 준비</span>
          </div>
          <div className="qm-script-lines">
            <div className="qm-script-line">
              <pre className="qm-script-text qm-meeting-prep-text">{meetingPrepSegmentText}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default QuickMeetingTranscript;
