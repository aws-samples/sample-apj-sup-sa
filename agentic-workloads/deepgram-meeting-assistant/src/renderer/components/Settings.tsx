import { useState } from 'react';
import { AWS_REGIONS, BEDROCK_MODEL_OPTIONS } from '../../shared/types';
import { useSettings } from '../hooks/useSettings';
import { maskAccessKey, maskSecretKey } from '../utils/masking';
import { useMeetingHistory } from '../hooks/useMeetingHistory';
import VocabularySettings from './VocabularySettings';
import VocabularyEditModal from './VocabularyEditModal';

function Settings() {
  const {
    settings,
    editValues,
    isLoading,
    isSaving,
    saveStatus,
    errorMessage,
    isConfigured,
    isElectron,
    handleInputChange,
    handleRegionChange,
    handleBedrockModelChange,
    saveSettings,
    clearSettings,
  } = useSettings();

  const { meetings, refresh: refreshMeetingHistory } = useMeetingHistory(1);
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [isDeletingAll, setIsDeletingAll] = useState(false);
  const [editingVocabularyId, setEditingVocabularyId] = useState<string | null>(null);
  const [vocabularyRefreshKey, setVocabularyRefreshKey] = useState(0);

  const refreshVocabularies = () => setVocabularyRefreshKey(prev => prev + 1);

  const getDisplayValue = (field: 'accessKeyId' | 'secretAccessKey') => {
    if (focusedField === field) {
      return editValues[field];
    }
    if (field === 'accessKeyId') {
      return maskAccessKey(editValues[field]);
    }
    return maskSecretKey(editValues[field]);
  };

  const handleClear = async () => {
    if (!confirm('AWS 자격 증명을 삭제하시겠습니까?')) return;
    await clearSettings();
  };

  const handleDeleteAllMeetings = async () => {
    const meetingCount = meetings.length;
    if (meetingCount === 0) {
      alert('삭제할 미팅이 없습니다.');
      return;
    }

    const confirmed = confirm(
      `모든 미팅 데이터(${meetingCount}개)를 삭제하시겠습니까?\n\n이 작업은 되돌릴 수 없습니다.`
    );
    if (!confirmed) return;

    if (!window.electronAPI) {
      alert('데스크톱 앱에서만 사용할 수 있습니다.');
      return;
    }

    setIsDeletingAll(true);
    try {
      const result = await window.electronAPI.deleteAllMeetings();
      if (result.success) {
        alert(`모든 미팅 데이터(${result.deletedCount ?? meetingCount}개)가 삭제되었습니다.`);
        refreshMeetingHistory();
      } else {
        alert(`삭제 실패: ${result.error || '알 수 없는 오류'}`);
      }
    } catch (error) {
      console.error('Failed to delete all meetings:', error);
      alert('미팅 데이터 삭제 중 오류가 발생했습니다.');
    } finally {
      setIsDeletingAll(false);
    }
  };

  if (isLoading) {
    return (
      <div className="settings-page">
        <div className="settings-loading" role="status" aria-label="설정을 불러오는 중">
          <span className="material-symbols-outlined spinning">progress_activity</span>
          <p>설정을 불러오는 중...</p>
        </div>
      </div>
    );
  }

  const showModelTip = settings.bedrock.correctionModelId !== settings.bedrock.translationModelId;

  return (
    <div className="settings-page">
      <h1 className="visually-hidden">Settings</h1>
      {/* AWS Configuration Card */}
      <div className="settings-card">
        <div className="settings-card-header">
          <h2>AWS Configuration</h2>
          <p>AWS Transcribe와 Bedrock 연결을 위한 자격 증명</p>
        </div>

        {!isElectron && (
          <div className="settings-alert warning">
            <span className="material-symbols-outlined">warning</span>
            <span>웹 브라우저 모드에서는 설정을 저장할 수 없습니다.</span>
          </div>
        )}

        <div className={`settings-badge ${isConfigured ? 'success' : 'error'}`}>
          <span className="material-symbols-outlined">
            {isConfigured ? 'check_circle' : 'error'}
          </span>
          <span>{isConfigured ? '연결됨' : '연결 필요'}</span>
        </div>

        <div className="settings-form">
          <div className="form-field">
            <label htmlFor="accessKeyId">Access Key ID</label>
            <input
              type="text"
              id="accessKeyId"
              value={getDisplayValue('accessKeyId')}
              onChange={(e) => handleInputChange('accessKeyId', e.target.value)}
              onFocus={() => setFocusedField('accessKeyId')}
              onBlur={() => setFocusedField(null)}
              placeholder="AKIA..."
              autoComplete="off"
              spellCheck={false}
              aria-invalid={saveStatus === 'error' && !!errorMessage && errorMessage.includes('Access Key')}
              aria-describedby={saveStatus === 'error' && !!errorMessage && errorMessage.includes('Access Key') ? 'error-accessKeyId' : undefined}
            />
            {saveStatus === 'error' && !!errorMessage && errorMessage.includes('Access Key') && (
              <span id="error-accessKeyId" className="form-error" role="alert">
                {errorMessage}
              </span>
            )}
          </div>

          <div className="form-field">
            <label htmlFor="secretAccessKey">Secret Access Key</label>
            <input
              type={focusedField === 'secretAccessKey' ? 'text' : 'password'}
              id="secretAccessKey"
              value={getDisplayValue('secretAccessKey')}
              onChange={(e) => handleInputChange('secretAccessKey', e.target.value)}
              onFocus={() => setFocusedField('secretAccessKey')}
              onBlur={() => setFocusedField(null)}
              placeholder="Secret key..."
              autoComplete="off"
              spellCheck={false}
              aria-invalid={saveStatus === 'error' && !!errorMessage && errorMessage.includes('Secret')}
              aria-describedby={saveStatus === 'error' && !!errorMessage && errorMessage.includes('Secret') ? 'error-secretAccessKey' : undefined}
            />
            {saveStatus === 'error' && !!errorMessage && errorMessage.includes('Secret') && (
              <span id="error-secretAccessKey" className="form-error" role="alert">
                {errorMessage}
              </span>
            )}
          </div>

          <div className="form-field">
            <label htmlFor="region">Region</label>
            <select
              id="region"
              value={settings.aws.region}
              onChange={(e) => handleRegionChange(e.target.value)}
            >
              {AWS_REGIONS.map((region) => (
                <option key={region.value} value={region.value}>
                  {region.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="settings-card-footer">
          <button type="button" className="btn-primary" onClick={saveSettings} disabled={isSaving || !isElectron}>
            {isSaving ? '저장 중...' : '저장'}
          </button>
          <button type="button" className="btn-secondary" onClick={handleClear} disabled={!isConfigured}>
            초기화
          </button>
          {saveStatus !== 'idle' && (
            <span className={`save-message ${saveStatus}`} title={errorMessage || ''}>
              {saveStatus === 'success' ? '저장됨' : (errorMessage || '저장 실패')}
            </span>
          )}
        </div>
      </div>

      {/* Model Configuration Card */}
      <div className="settings-card">
        <div className="settings-card-header">
          <h2>Model Configuration</h2>
          <p>AI 기능에 사용할 Bedrock 모델 선택</p>
        </div>

        {showModelTip && (
          <div className="settings-alert info">
            <span className="material-symbols-outlined">lightbulb</span>
            <span>보정과 번역에 같은 모델을 사용하면 더 빠릅니다</span>
          </div>
        )}

        <div className="settings-form">
          <div className="form-row">
            <div className="form-field">
              <label htmlFor="correctionModelId">보정</label>
              <select
                id="correctionModelId"
                value={settings.bedrock.correctionModelId}
                onChange={(e) => handleBedrockModelChange('correctionModelId', e.target.value)}
              >
                {BEDROCK_MODEL_OPTIONS.map((model) => (
                  <option key={model.value} value={model.value}>
                    {model.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-field">
              <label htmlFor="translationModelId">번역</label>
              <select
                id="translationModelId"
                value={settings.bedrock.translationModelId}
                onChange={(e) => handleBedrockModelChange('translationModelId', e.target.value)}
              >
                {BEDROCK_MODEL_OPTIONS.map((model) => (
                  <option key={model.value} value={model.value}>
                    {model.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="form-field half">
            <label htmlFor="summaryModelId">요약</label>
            <select
              id="summaryModelId"
              value={settings.bedrock.summaryModelId}
              onChange={(e) => handleBedrockModelChange('summaryModelId', e.target.value)}
            >
              {BEDROCK_MODEL_OPTIONS.map((model) => (
                <option key={model.value} value={model.value}>
                  {model.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="settings-card-footer">
          <button type="button" className="btn-primary" onClick={saveSettings} disabled={isSaving || !isElectron}>
            {isSaving ? '저장 중...' : '저장'}
          </button>
        </div>
      </div>

      {/* Vocabulary Management Card */}
      <VocabularySettings 
        key={vocabularyRefreshKey} 
        onEditVocabulary={setEditingVocabularyId} 
      />

      {/* Vocabulary Edit Modal */}
      {editingVocabularyId && (
        <VocabularyEditModal
          vocabularyId={editingVocabularyId}
          onClose={() => setEditingVocabularyId(null)}
          onSave={refreshVocabularies}
        />
      )}

      {/* Data Management Card */}
      <div className="settings-card">
        <div className="settings-card-header">
          <h2>데이터 관리</h2>
          <p>로컬에 저장된 미팅 데이터 관리</p>
        </div>

        <div className="settings-alert warning">
          <span className="material-symbols-outlined">warning</span>
          <span>데이터 삭제는 되돌릴 수 없습니다. 신중하게 결정하세요.</span>
        </div>

        <div className="settings-form">
          <div className="form-field">
            <span className="form-label">저장된 미팅 수</span>
            <div className="form-value">{meetings.length}개</div>
          </div>
        </div>

        <div className="settings-card-footer">
          <button
            type="button"
            className="btn-danger"
            onClick={handleDeleteAllMeetings}
            disabled={isDeletingAll || !isElectron || meetings.length === 0}
          >
            {isDeletingAll ? '삭제 중...' : '모든 미팅 데이터 삭제'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default Settings;
