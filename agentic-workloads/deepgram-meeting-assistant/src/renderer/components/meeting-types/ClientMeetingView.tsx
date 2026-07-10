import { useState, useEffect } from 'react';
import type { MeetingWorkspaceProps } from '../meeting/MeetingWorkspace';
import MeetingWorkspace from '../meeting/MeetingWorkspace';
import { MeetingPrepModal } from '../meeting';
import type { ConnectionStatus, McpTool } from '../../../shared/types/mcp';

function ClientMeetingView(props: MeetingWorkspaceProps) {
  const [isPrepModalOpen, setIsPrepModalOpen] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected');
  const [tools, setTools] = useState<McpTool[]>([]);

  // prepData는 상위 컴포넌트(MeetingView)에서 관리
  const { prepData, onPrepDataChange } = props;

  // 컴포넌트 마운트 시 연결 상태 확인
  useEffect(() => {
    const checkStatus = async () => {
      const status = await window.electronAPI.mcp.getStatus();
      setConnectionStatus(status);
    };
    checkStatus();
  }, []);

  async function connectMcpServer(): Promise<void> {
    if (connectionStatus === 'connected') {
      return;
    }

    setConnectionStatus('connecting');

    try {
      const result = await window.electronAPI.mcp.connect();
      if (result.success) {
        setConnectionStatus('connected');
      } else {
        setConnectionStatus('error');
      }
    } catch (error) {
      setConnectionStatus('error');
    }
  }

  async function disconnectMcpServer(): Promise<void> {
    if (connectionStatus !== 'connected') {
      return;
    }

    try {
      const result = await window.electronAPI.mcp.disconnect();
      if (result.success) {
        setConnectionStatus('disconnected');
        setTools([]);
      }
    } catch (error) {
      console.error('Failed to disconnect:', error);
    }
  }

  async function listMcpTools(): Promise<void> {
    if (connectionStatus !== 'connected') {
      return;
    }

    try {
      const result = await window.electronAPI.mcp.listTools();
      if (result.success && result.data) {
        setTools(result.data);
      }
    } catch (error) {
      console.error('Failed to list tools:', error);
    }
  }

  // 미팅 준비 모달 열기 버튼 패널
  const meetingPrepPanel = (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header">
        <h4>미팅 준비</h4>
      </div>
      <div className="qm-ai-panel-body">
        <button
          className="qm-right-panel-action"
          onClick={() => setIsPrepModalOpen(true)}
          style={{ width: '100%', justifyContent: 'center' }}
        >
          <span className="material-symbols-outlined">event_note</span>
          미팅 준비 작성
        </button>
      </div>
    </div>
  );

  // MCP 서버 패널
  const mcpPanel = (
    <div className="qm-right-panel-card">
      <div className="qm-right-panel-card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h4>MCP 서버</h4>
        <div className="mcp-status" style={{ margin: 0 }}>
          <span className={`status-indicator ${connectionStatus}`}></span>
          <span className="status-text" style={{ marginLeft: '6px' }}>
            {connectionStatus === 'disconnected' && '연결 안됨'}
            {connectionStatus === 'connecting' && '...'}
            {connectionStatus === 'connected' && '연결됨'}
            {connectionStatus === 'error' && '오류'}
          </span>
        </div>
      </div>
      <div className="qm-ai-panel-body">
        <div className="mcp-buttons">
          {connectionStatus !== 'connected' ? (
            <button
              className={`mcp-btn ${connectionStatus === 'connecting' ? 'connecting' : ''}`}
              onClick={connectMcpServer}
              disabled={connectionStatus === 'connecting'}
              style={{ width: '100%', justifyContent: 'center' }}
            >
              <span className="material-symbols-outlined">
                {connectionStatus === 'connecting' ? 'sync' : 'power'}
              </span>
              {connectionStatus === 'connecting' ? '연결 중...' : '서버 연결'}
            </button>
          ) : (
            <button
              className="mcp-btn connected"
              onClick={disconnectMcpServer}
              style={{ width: '100%', justifyContent: 'center' }}
            >
              <span className="material-symbols-outlined">power_off</span>
              연결 해제
            </button>
          )}
          <button
            className="mcp-btn"
            onClick={listMcpTools}
            disabled={connectionStatus !== 'connected'}
            style={{ width: '100%', justifyContent: 'center' }}
          >
            <span className="material-symbols-outlined">build</span>
            도구 목록
          </button>
        </div>
        {tools.length > 0 && (
          <div className="mcp-tools-list">
            <h5>사용 가능한 도구 ({tools.length})</h5>
            <ul>
              {tools.map((tool) => (
                <li key={tool.name} title={tool.description}>
                  {tool.name}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="client-meeting-view">
      <div className="client-meeting-layout">
        <div className="meeting-workspace-section">
          <MeetingWorkspace
            {...props}
            prepData={prepData}
            rightPanelContent={
              <div className="qm-right-panel-stack">
                {meetingPrepPanel}
                {mcpPanel}
              </div>
            }
          />
        </div>
      </div>

      <MeetingPrepModal
        isOpen={isPrepModalOpen}
        onClose={() => setIsPrepModalOpen(false)}
        onComplete={(data) => {
          onPrepDataChange?.(data);
          setIsPrepModalOpen(false);
        }}
        initialData={prepData ?? undefined}
      />
    </div>
  );
}

export default ClientMeetingView;