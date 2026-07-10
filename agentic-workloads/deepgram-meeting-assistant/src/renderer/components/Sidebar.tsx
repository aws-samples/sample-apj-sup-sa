import { useState, useEffect, useRef } from 'react';
import { MEETING_TYPES, NavItem, type Meeting } from '../../shared/types';

interface SidebarProps {
  activeNav: NavItem;
  onNavChange: (nav: NavItem) => void;
  meetings: Meeting[];
  selectedMeetingId?: string | null;
  onMeetingSelect?: (meeting: Meeting) => void;
  onMeetingDelete?: (meetingId: string) => void;
  isHistoryLoading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;
}

interface ContextMenuState {
  meetingId: string;
  x: number;
  y: number;
}

const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  month: 'numeric',
  day: 'numeric',
});
const timeFormatter = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit',
  minute: '2-digit',
});

function formatMeetingDate(value: Date | string | undefined): string {
  if (!value) return '';
  const date = value instanceof Date ? value : new Date(value);
  const dateText = dateFormatter.format(date);
  const timeText = timeFormatter.format(date);
  return `${dateText} ${timeText}`;
}

function Sidebar({
  activeNav,
  onNavChange,
  meetings,
  selectedMeetingId = null,
  onMeetingSelect,
  onMeetingDelete,
  isHistoryLoading = false,
  hasMore = false,
  onLoadMore,
}: SidebarProps) {
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const contextMenuRef = useRef<HTMLDivElement>(null);
  const navItems: { id: NavItem; label: string; icon: string }[] = [
    { id: 'home', label: 'Home', icon: 'home' },
    { id: 'settings', label: 'Settings', icon: 'settings' },
  ];

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(event.target as Node)) {
        setContextMenu(null);
      }
    };

    if (contextMenu) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [contextMenu]);

  const handleContextMenu = (e: React.MouseEvent, meeting: Meeting) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({
      meetingId: meeting.id,
      x: e.clientX,
      y: e.clientY,
    });
  };

  const handleDelete = (meetingId: string) => {
    if (onMeetingDelete) {
      onMeetingDelete(meetingId);
    }
    setContextMenu(null);
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-content">
        <nav className="nav">
          {navItems.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`nav-item ${activeNav === item.id ? 'active' : ''}`}
              onClick={() => onNavChange(item.id)}
              aria-current={activeNav === item.id ? 'page' : undefined}
            >
              <span className="material-symbols-outlined">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-history">
          <div className="sidebar-history-header">
            <span>Meeting History</span>
            {isHistoryLoading && <span className="sidebar-history-loading">로딩 중...</span>}
          </div>
          {meetings.length === 0 ? (
            <div className="sidebar-history-empty">기록된 미팅이 없습니다.</div>
          ) : (
            <>
              <ul className="sidebar-history-list">
                {meetings.map((meeting) => {
                  const meetingTypeConfig = MEETING_TYPES.find((type) => type.id === meeting.type);
                  const icon = meetingTypeConfig?.icon ?? 'description';
                  const label = meetingTypeConfig?.label ?? 'Meeting';
                  const dateValue = meeting.startedAt ?? meeting.createdAt;
                  const formattedDate = formatMeetingDate(dateValue);
                  const isActive = selectedMeetingId === meeting.id;

                  return (
                    <li key={meeting.id} className="sidebar-history-item">
                      <button
                        type="button"
                        className={`sidebar-history-button ${isActive ? 'active' : ''}`}
                        onClick={() => onMeetingSelect?.(meeting)}
                        onContextMenu={(e) => handleContextMenu(e, meeting)}
                        disabled={!onMeetingSelect}
                        title={`${meeting.title} (${formattedDate})`}
                      >
                        <div className="history-item-title">
                          <span className="material-symbols-outlined history-item-icon">{icon}</span>
                          <span className="history-item-title-text">{meeting.title}</span>
                        </div>
                        <div className="history-item-meta">
                          <span className="history-item-type">{label}</span>
                          <span className="history-item-date">{formattedDate}</span>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
              {hasMore && (
                <button
                  type="button"
                  className="sidebar-load-more-btn"
                  onClick={onLoadMore}
                  disabled={isHistoryLoading}
                >
                  {isHistoryLoading ? '로딩 중...' : '더 보기'}
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {contextMenu && (
        <div
          ref={contextMenuRef}
          className="sidebar-context-menu"
          style={{
            position: 'fixed',
            left: `${contextMenu.x}px`,
            top: `${contextMenu.y}px`,
          }}
        >
          <button
            type="button"
            className="context-menu-item danger"
            onClick={() => handleDelete(contextMenu.meetingId)}
          >
            <span className="material-symbols-outlined">delete</span>
            <span>삭제</span>
          </button>
        </div>
      )}
    </aside>
  );
}

export default Sidebar;
