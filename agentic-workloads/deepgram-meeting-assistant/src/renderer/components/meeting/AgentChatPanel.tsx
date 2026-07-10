/**
 * AgentChatPanel
 *
 * 미팅 종료 후 회의록 대화 agent와 텍스트 채팅하는 우측 패널.
 *  - 메시지 버블(user/assistant/system)
 *  - 입력창(Enter 전송, Shift+Enter 개행)
 *  - pendingAction 컨펌 카드(회의록 수정 / CRM 로깅) — [승인]/[취소]
 *  - 상단 MCP(CRM) 연결 상태 + 미연결 시 연결 버튼
 *
 * enabled=false(녹음 중 등)면 입력을 비활성화한다.
 */
import { useEffect, useRef, useState } from 'react';
import type { ConnectionStatus } from '../../../shared/types/mcp';
import type { AgentPendingAction } from '../../../shared/types/agent';
import { useAgentChat } from '../../hooks/useAgentChat';

interface AgentChatPanelProps {
  meetingId: string | null | undefined;
  /** 채팅 활성 조건(미팅 종료/히스토리 조회 시 true). */
  enabled: boolean;
  /** 회의록(요약)이 아직 없으면 안내 표시용. */
  hasMeetingRecord: boolean;
}

function PendingActionCard({
  action,
  onResolve,
}: {
  action: AgentPendingAction;
  onResolve: (id: string, approved: boolean) => void;
}) {
  const title =
    action.kind === 'meeting_edit'
      ? `회의록 수정 제안 (${String(action.args.field ?? 'topics')})`
      : `CRM 기록 제안: ${action.name}`;
  return (
    <div className="qm-agent-pending-card">
      <div className="qm-agent-pending-title">{title}</div>
      <pre className="qm-agent-pending-args">{JSON.stringify(action.args, null, 2)}</pre>
      <div className="qm-agent-pending-actions">
        <button className="mcp-btn" onClick={() => onResolve(action.id, true)}>
          승인
        </button>
        <button className="mcp-btn mcp-btn--ghost" onClick={() => onResolve(action.id, false)}>
          취소
        </button>
      </div>
    </div>
  );
}

function AgentChatPanel({ meetingId, enabled, hasMeetingRecord }: AgentChatPanelProps) {
  const { messages, pendingActions, isSending, error, send, resolve } = useAgentChat(meetingId);
  const [input, setInput] = useState('');
  const [mcpStatus, setMcpStatus] = useState<ConnectionStatus>('disconnected');
  const scrollRef = useRef<HTMLDivElement>(null);

  // MCP 상태 폴링(최초 1회) — CRM 로깅 가능 여부 표시.
  useEffect(() => {
    if (!window.electronAPI?.mcp) return;
    void window.electronAPI.mcp.getStatus().then(setMcpStatus).catch(() => {});
  }, []);

  // 새 메시지/카드 도착 시 맨 아래로 스크롤.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pendingActions]);

  const connectMcp = async () => {
    if (!window.electronAPI?.mcp) return;
    setMcpStatus('connecting');
    try {
      const r = await window.electronAPI.mcp.connect();
      setMcpStatus(r.success ? 'connected' : 'error');
    } catch {
      setMcpStatus('error');
    }
  };

  const handleSend = () => {
    if (!input.trim() || isSending) return;
    void send(input);
    setInput('');
  };

  return (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header qm-agent-header">
        <h4>💬 회의록 어시스턴트</h4>
        <span
          className={`qm-agent-crm-badge qm-agent-crm-badge--${mcpStatus === 'connected' ? 'on' : 'off'}`}
          title={mcpStatus === 'connected' ? 'CRM 연결됨' : 'CRM 미연결'}
        >
          <span className="qm-agent-crm-dot" />
          CRM
        </span>
      </div>

      <div className="qm-ai-panel-body qm-agent-body">
        {!enabled ? (
          <div className="qm-empty-state qm-right-panel-empty">
            회의가 끝나면 회의록을 바탕으로 대화하며 수정하고 CRM에 기록할 수 있습니다.
          </div>
        ) : (
          <>
            {mcpStatus !== 'connected' && (
              <div className="qm-agent-mcp-hint">
                CRM 기록을 쓰려면 MCP 서버에 연결하세요.
                <button className="mcp-btn" onClick={connectMcp} disabled={mcpStatus === 'connecting'}>
                  {mcpStatus === 'connecting' ? '연결 중…' : 'CRM 연결'}
                </button>
              </div>
            )}

            <div className="qm-agent-messages" ref={scrollRef}>
              {messages.length === 0 && !hasMeetingRecord && (
                <div className="qm-empty-state qm-right-panel-empty">
                  먼저 "AI 회의록"을 생성한 뒤 대화하면 더 정확합니다.
                </div>
              )}
              {messages.length === 0 && hasMeetingRecord && (
                <div className="qm-empty-state qm-right-panel-empty">
                  예: "액션 아이템에 'X 팔로업' 추가해줘", "이 회의를 CRM tech activity로 기록해줘"
                </div>
              )}
              {messages.map((m, i) => (
                <div key={i} className={`qm-agent-msg qm-agent-msg--${m.role}`}>
                  {m.text}
                </div>
              ))}
              {pendingActions.map((a) => (
                <PendingActionCard key={a.id} action={a} onResolve={resolve} />
              ))}
              {isSending && <div className="qm-agent-msg qm-agent-msg--assistant">생각 중…</div>}
            </div>

            {error && <div className="qm-right-panel-error">{error}</div>}

            <div className="qm-agent-input">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder="회의록 수정·CRM 기록을 요청하세요 (Enter 전송)"
                rows={2}
                disabled={isSending}
              />
              <button className="mcp-btn" onClick={handleSend} disabled={isSending || !input.trim()}>
                전송
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default AgentChatPanel;
