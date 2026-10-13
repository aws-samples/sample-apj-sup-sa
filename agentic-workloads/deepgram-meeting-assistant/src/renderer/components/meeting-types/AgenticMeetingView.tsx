import { cloneElement, useEffect, useState } from 'react';
import type { MeetingWorkspaceProps } from '../meeting/MeetingWorkspace';
import MeetingWorkspace from '../meeting/MeetingWorkspace';
import AgentChatPanel from '../meeting/AgentChatPanel';
import useAssistant from '../../hooks/useAssistant';

function AgenticMeetingView(props: MeetingWorkspaceProps) {
  const isRecording = props.recordingState.status === 'recording';
  const [serverError, setServerError] = useState<string | null>(null);
  const assistant = useAssistant();

  // Post-Meeting Agent: 미팅 종료(또는 히스토리 조회) 시에만 활성.
  const meetingId = props.recordingState.meetingId ?? null;
  const isCompleted = props.recordingState.status === 'completed';
  const agentEnabled = isCompleted || (props.isViewingHistory ?? false);
  const hasMeetingRecord = Boolean(props.summary || props.conversationLog);
  // 탭별 우측 패널 분리:
  //  - 스크립트 탭: 실시간 패널(Voice Assistant + Pipecat 상태) — 녹음/실시간용
  //  - AI 회의록(summary) 탭: 회의록 어시스턴트 — 미팅 후 회의록 대화용
  //  - 대화요약(conversation) 탭: 둘 다 불필요
  const showAgentPanel = props.activeTab === 'summary';
  const showLivePanels = props.activeTab === 'script';

  // 전사 에러(서버 미연결 등)를 감지해 배지 상태 갱신
  useEffect(() => {
    if (!window.electronAPI?.onTranscriptionError) return;
    const off = window.electronAPI.onTranscriptionError((data) => {
      setServerError(data.error);
    });
    return off;
  }, []);

  const statusPanel = (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header">
        <h4>Pipecat Pipeline</h4>
      </div>
      <div className="qm-ai-panel-body">
        {serverError ? (
          <div className="qm-empty-state qm-right-panel-empty">
            서버 미연결: {serverError}
            <br />
            <code>cd server &amp;&amp; python bot.py</code> 로 서버를 먼저 실행하세요.
          </div>
        ) : (
          <div className="qm-empty-state qm-right-panel-empty">
            {isRecording
              ? '🟢 Pipecat 파이프라인 활성 (STT + Bedrock via local server)'
              : '대기 중 — 녹음을 시작하면 로컬 Pipecat 서버에 연결합니다.'}
          </div>
        )}
      </div>
    </div>
  );

  const assistantPanel = (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header">
        <h4>🎙️ Voice Assistant</h4>
      </div>
      <div className="qm-ai-panel-body">
        {assistant.query === null ? (
          <div className="qm-empty-state qm-right-panel-empty">
            "Hey assistant ..." 라고 말하면 회의 맥락을 바탕으로 답합니다.
          </div>
        ) : (
          <div className="qm-assistant-exchange">
            <div className="qm-assistant-query">
              <strong>Q.</strong> {assistant.query}
            </div>
            <div className="qm-assistant-response">
              {assistant.responseText || (assistant.isResponding ? '응답 생성 중…' : '')}
              {assistant.isResponding && <span className="qm-assistant-cursor">▋</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  );

  // 현재 탭에 맞는 우측 패널만 모은다. 아무것도 없으면 undefined를 넘겨
  // MeetingWorkspace의 기본 플레이스홀더가 표시되게 한다.
  const panels = [
    showAgentPanel && (
      <AgentChatPanel
        key="agent"
        meetingId={meetingId}
        enabled={agentEnabled}
        hasMeetingRecord={hasMeetingRecord}
      />
    ),
    showLivePanels && cloneElement(assistantPanel, { key: 'assistant' }),
    showLivePanels && cloneElement(statusPanel, { key: 'status' }),
  ].filter(Boolean);

  return (
    <MeetingWorkspace
      {...props}
      rightPanelContent={
        panels.length > 0 ? <div className="qm-right-panel-stack">{panels}</div> : undefined
      }
    />
  );
}

export default AgenticMeetingView;
