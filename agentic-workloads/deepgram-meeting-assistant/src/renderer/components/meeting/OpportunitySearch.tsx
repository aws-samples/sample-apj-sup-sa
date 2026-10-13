/**
 * OpportunitySearch Component
 * 
 * Opportunity 검색 및 결과 표시 컴포넌트입니다.
 * MCP 연결 상태 표시, Account ID 검색, 페이지네이션을 처리합니다.
 * 
 * ORCH-003: MeetingPrepModal 분리 - 검색 UI를 별도 컴포넌트로 분리
 */

import { useCallback } from 'react';
import type { OpportunityInfo } from '@shared/types/meeting-prep';
import type { ConnectionStatus } from '@shared/types/mcp';
import type { McpState, SearchState } from '../../hooks/useMeetingPrep';

const WHITESPACE_REGEX = /\s+/g;

export interface OpportunitySearchProps {
  mcpState: McpState;
  searchState: SearchState;
  selectedOpportunityId: string | null;
  onConnectMcp: () => Promise<void>;
  onAccountIdChange: (value: string) => void;
  onUserAliasChange: (value: string) => void;
  onSearch: (cursor?: string | null) => Promise<void>;
  onSelectOpportunity: (opportunity: OpportunityInfo) => void;
}

/**
 * MCP 연결 상태 인디케이터
 */
function McpStatusIndicator({
  mcpState,
  onReconnect,
}: {
  mcpState: McpState;
  onReconnect: () => Promise<void>;
}) {
  return (
    <div className="meeting-prep-mcp-status">
      <div className={`mcp-status-indicator ${mcpState.status}`}>
        <span className={`status-dot ${mcpState.status}`}></span>
        <span className="status-text">
          {mcpState.status === 'disconnected' && 'MCP 서버 연결 안됨'}
          {mcpState.status === 'connecting' && 'MCP 서버 연결 중...'}
          {mcpState.status === 'connected' && 'MCP 서버 연결됨'}
          {mcpState.status === 'error' && 'MCP 서버 연결 오류'}
        </span>
        {mcpState.status === 'connecting' && (
          <span className="material-symbols-outlined mcp-loading-icon">sync</span>
        )}
        {mcpState.status === 'connected' && (
          <span className="material-symbols-outlined mcp-connected-icon">check_circle</span>
        )}
      </div>
      {mcpState.status === 'error' && (
        <div className="mcp-error-section">
          {mcpState.error && (
            <div className="mcp-error-message">
              <span className="material-symbols-outlined">error</span>
              <span>{mcpState.error}</span>
            </div>
          )}
          <button
            type="button"
            className="btn-secondary mcp-reconnect-btn"
            onClick={onReconnect}
          >
            <span className="material-symbols-outlined">refresh</span>
            재연결
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * 검색 결과 항목
 */
function SearchResultItem({
  opportunity,
  isSelected,
  onSelect,
}: {
  opportunity: OpportunityInfo;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect();
    }
  };

  return (
    <li
      className={`search-result-item ${isSelected ? 'selected' : ''}`}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <div className="search-result-main">
        <span className="search-result-name">{opportunity.name}</span>
        <span
          className={`search-result-stage stage-${opportunity.stageName?.toLowerCase().replace(WHITESPACE_REGEX, '-')}`}
        >
          {opportunity.stageName}
        </span>
      </div>
      <div className="search-result-details">
        <span className="search-result-account">
          <span className="material-symbols-outlined">business</span>
          {opportunity.account?.name || opportunity.accountName || '-'}
        </span>
        <span className="search-result-owner">
          <span className="material-symbols-outlined">person</span>
          {opportunity.owner?.name || '-'}
        </span>
        {opportunity.closeDate && (
          <span className="search-result-date">
            <span className="material-symbols-outlined">event</span>
            {opportunity.closeDate}
          </span>
        )}
      </div>
    </li>
  );
}

/**
 * Opportunity 검색 컴포넌트
 * Requirements: 4.1-4.6, 5.1-5.4, 6.1
 */
function OpportunitySearch({
  mcpState,
  searchState,
  selectedOpportunityId,
  onConnectMcp,
  onAccountIdChange,
  onUserAliasChange,
  onSearch,
  onSelectOpportunity,
}: OpportunitySearchProps) {
  const handleSearchKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'Enter' && !searchState.isSearching && mcpState.status === 'connected') {
        onSearch();
      }
    },
    [onSearch, searchState.isSearching, mcpState.status]
  );

  const isSearchDisabled =
    mcpState.status !== 'connected' ||
    searchState.isSearching ||
    !searchState.accountIdInput.trim();

  return (
    <div className="meeting-prep-search-section">
      <h3 className="meeting-prep-section-title">Opportunity 검색</h3>

      {/* MCP 연결 상태 인디케이터 */}
      <McpStatusIndicator mcpState={mcpState} onReconnect={onConnectMcp} />

      {/* 사용자 Alias 입력 필드 (Requirements: 6.4) */}
      <div className="meeting-prep-alias-row">
        <div className="form-field meeting-prep-alias-field">
          <label htmlFor="prep-user-alias">사용자 Alias</label>
          <input
            id="prep-user-alias"
            type="text"
            value={searchState.userAlias}
            onChange={(e) => onUserAliasChange(e.target.value)}
            placeholder="Task 조회를 위한 Alias 입력"
            className="meeting-prep-alias-input"
          />
        </div>
      </div>

      {/* 검색 입력 필드 및 버튼 */}
      <div className="meeting-prep-search-row">
        <div className="meeting-prep-search-input-wrapper">
          <input
            id="prep-account-id"
            type="text"
            value={searchState.accountIdInput}
            onChange={(e) => onAccountIdChange(e.target.value)}
            onKeyDown={handleSearchKeyDown}
            placeholder="Account ID를 입력하세요"
            disabled={mcpState.status !== 'connected' || searchState.isSearching}
            className="meeting-prep-search-input"
          />
        </div>
        <button
          type="button"
          className="btn-primary meeting-prep-search-btn"
          onClick={() => onSearch()}
          disabled={isSearchDisabled}
        >
          {searchState.isSearching ? (
            <>
              <span className="material-symbols-outlined meeting-prep-search-loading">sync</span>
              검색 중...
            </>
          ) : (
            <>
              <span className="material-symbols-outlined">search</span>
              검색
            </>
          )}
        </button>
      </div>

      {/* MCP 서버 미연결 시 안내 메시지 */}
      {mcpState.status !== 'connected' && (
        <div className="meeting-prep-search-disabled-notice">
          <span className="material-symbols-outlined">info</span>
          <span>MCP 서버에 연결되어야 검색할 수 있습니다.</span>
        </div>
      )}

      {/* 검색 에러 메시지 (Requirements: 4.5) */}
      {searchState.error && (
        <div className="meeting-prep-search-error">
          <span className="material-symbols-outlined">error</span>
          <span>{searchState.error}</span>
        </div>
      )}

      {/* 검색 결과 없음 메시지 (Requirements: 4.4) */}
      {searchState.hasSearched &&
        !searchState.isSearching &&
        !searchState.error &&
        searchState.results.length === 0 && (
          <div className="meeting-prep-search-empty">
            <span className="material-symbols-outlined">search_off</span>
            <span>검색 결과가 없습니다</span>
          </div>
        )}

      {/* 검색 결과 목록 (Requirements: 4.3, 6.1) */}
      {searchState.results.length > 0 && (
        <div className="meeting-prep-search-results">
          <div className="search-results-header">
            <span className="search-results-count">{searchState.results.length}개의 Opportunity</span>
          </div>
          <ul className="search-results-list">
            {searchState.results.map((opp) => (
              <SearchResultItem
                key={opp.id}
                opportunity={opp}
                isSelected={selectedOpportunityId === opp.id}
                onSelect={() => onSelectOpportunity(opp)}
              />
            ))}
          </ul>

          {/* 더 보기 버튼 (Requirements: 5.2, 5.3) */}
          {searchState.hasNextPage && (
            <button
              type="button"
              className="btn-secondary meeting-prep-load-more"
              onClick={() => onSearch(searchState.cursor)}
              disabled={searchState.isLoadingMore}
            >
              {searchState.isLoadingMore ? (
                <>
                  <span className="material-symbols-outlined meeting-prep-search-loading">sync</span>
                  로딩 중...
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined">expand_more</span>
                  더 보기
                </>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default OpportunitySearch;
