import { useState, useEffect, useCallback } from 'react';
import { 
  Vocabulary, 
  VocabularyEntry, 
  VocabularyLanguage 
} from '../../shared/types/vocabulary';

interface VocabularyEditModalProps {
  vocabularyId: string;
  onClose: () => void;
  onSave?: () => void;
}

const LANGUAGES: { value: VocabularyLanguage; label: string }[] = [
  { value: 'ko-KR', label: '한국어 (ko-KR)' },
  { value: 'en-US', label: '영어 (en-US)' },
];

export default function VocabularyEditModal({ vocabularyId, onClose, onSave }: VocabularyEditModalProps) {
  const [vocabulary, setVocabulary] = useState<Vocabulary | null>(null);
  const [entries, setEntries] = useState<VocabularyEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [isCheckingStatus, setIsCheckingStatus] = useState(false);
  
  // Metadata state
  const [name, setName] = useState('');
  const [languageCode, setLanguageCode] = useState<VocabularyLanguage>('ko-KR');

  const fetchData = useCallback(async () => {
    if (!window.electronAPI) return;
    setIsLoading(true);
    try {
      const v = await window.electronAPI.vocabulary.get(vocabularyId);
      if (v) {
        setVocabulary(v);
        setName(v.name);
        setLanguageCode(v.languageCode);
      }
      
      const e = await window.electronAPI.vocabulary.listEntries(vocabularyId);
      setEntries(e);
    } catch (error) {
      console.error('Failed to fetch vocabulary data:', error);
      alert('용어집 데이터를 불러오는 데 실패했습니다.');
    } finally {
      setIsLoading(false);
    }
  }, [vocabularyId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleSaveMetadata = async () => {
    if (!window.electronAPI || !vocabulary || vocabulary.isBuiltin) return;
    
    setIsSaving(true);
    try {
      await window.electronAPI.vocabulary.update(vocabularyId, { name, languageCode });
      onSave?.();
    } catch (error) {
      console.error('Failed to update vocabulary:', error);
      alert('용어집 정보 저장에 실패했습니다.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleAddEntry = async () => {
    if (!window.electronAPI || !vocabulary || vocabulary.isBuiltin) return;
    
    try {
      await window.electronAPI.vocabulary.addEntry({
        vocabularyId,
        phrase: '',
        soundsLike: '',
        displayAs: '',
      });
      const updatedEntries = await window.electronAPI.vocabulary.listEntries(vocabularyId);
      setEntries(updatedEntries);
      onSave?.();
    } catch (error) {
      console.error('Failed to add entry:', error);
    }
  };

  const handleUpdateEntry = async (entryId: string, updates: { phrase?: string; soundsLike?: string; displayAs?: string }) => {
    if (!window.electronAPI || !vocabulary || vocabulary.isBuiltin) return;
    
    try {
      await window.electronAPI.vocabulary.updateEntry(entryId, updates);
      // Update local state to avoid full refresh if possible, but for simplicity:
      setEntries(prev => prev.map(e => e.id === entryId ? { ...e, ...updates } : e));
    } catch (error) {
      console.error('Failed to update entry:', error);
    }
  };

  const handleRemoveEntry = async (entryId: string) => {
    if (!window.electronAPI || !vocabulary || vocabulary.isBuiltin) return;
    if (!confirm('이 항목을 삭제하시겠습니까?')) return;

    try {
      const success = await window.electronAPI.vocabulary.removeEntry(entryId);
      if (success) {
        setEntries(prev => prev.filter(e => e.id !== entryId));
        onSave?.();
      }
    } catch (error) {
      console.error('Failed to remove entry:', error);
    }
  };

  const handleSyncToAws = async () => {
    if (!window.electronAPI || !vocabulary) return;
    
    setIsSyncing(true);
    try {
      const result = await window.electronAPI.vocabulary.syncToAws(vocabularyId);
      if (result.success) {
        // Immediately check status to update local state
        await window.electronAPI.vocabulary.checkStatus(vocabularyId);
        await fetchData();
        onSave?.();
      } else {
        alert(`AWS 동기화 실패: ${result.error}`);
      }
    } catch (error: any) {
      console.error('Failed to sync to AWS:', error);
      alert(`AWS 동기화 중 오류가 발생했습니다: ${error.message}`);
    } finally {
      setIsSyncing(false);
    }
  };

  const handleCheckStatus = async () => {
    if (!window.electronAPI || !vocabulary) return;
    
    setIsCheckingStatus(true);
    try {
      await window.electronAPI.vocabulary.checkStatus(vocabularyId);
      await fetchData();
      onSave?.();
    } catch (error) {
      console.error('Failed to check status:', error);
    } finally {
      setIsCheckingStatus(false);
    }
  };

  if (isLoading) {
    return (
      <div className="modal-overlay">
        <div className="modal-content vocabulary-edit-modal">
          <div className="modal-body flex items-center justify-center p-12">
            <span className="material-symbols-outlined spinning">progress_activity</span>
            <span className="ml-2">불러오는 중...</span>
          </div>
        </div>
      </div>
    );
  }

  if (!vocabulary) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content vocabulary-edit-modal">
        <div className="modal-header">
          <div className="flex items-center gap-3">
            <h2>용어집 편집: {vocabulary.name}</h2>
            <span className={`vocabulary-status ${vocabulary.awsStatus} text-xs px-2 py-0.5 rounded-full border`}>
              {vocabulary.awsStatus === 'READY' && '● 동기화됨'}
              {vocabulary.awsStatus === 'PENDING' && '● 동기화 진행 중'}
              {vocabulary.awsStatus === 'FAILED' && '● 동기화 실패'}
              {vocabulary.awsStatus === 'NOT_SYNCED' && '○ 동기화 필요'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button 
              type="button" 
              className="btn-icon" 
              onClick={handleCheckStatus}
              disabled={isCheckingStatus}
              title="상태 확인"
            >
              <span className={`material-symbols-outlined ${isCheckingStatus ? 'spinning' : ''}`}>refresh</span>
            </button>
            <button type="button" className="btn-icon" onClick={onClose}>
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        </div>
        
        <div className="modal-body">
          <div className="vocabulary-meta-section">
            <div className="form-row">
              <div className="form-field">
                <label htmlFor="vocab-name">이름</label>
                <input 
                  id="vocab-name"
                  type="text" 
                  value={name} 
                  onChange={(e) => setName(e.target.value)}
                  disabled={vocabulary.isBuiltin}
                  className={vocabulary.isBuiltin ? 'readonly' : ''}
                  placeholder="용어집 이름"
                />
              </div>
              <div className="form-field">
                <label htmlFor="vocab-lang">언어</label>
                <select 
                  id="vocab-lang"
                  value={languageCode} 
                  onChange={(e) => setLanguageCode(e.target.value as VocabularyLanguage)}
                  disabled={vocabulary.isBuiltin}
                  className={vocabulary.isBuiltin ? 'readonly' : ''}
                >
                  {LANGUAGES.map(lang => (
                    <option key={lang.value} value={lang.value}>{lang.label}</option>
                  ))}
                </select>
              </div>
            </div>
            {!vocabulary.isBuiltin && (
              <button 
                type="button" 
                className="btn-secondary mt-2" 
                onClick={handleSaveMetadata}
                disabled={isSaving}
              >
                기본 정보 저장
              </button>
            )}
          </div>

          <div className="vocabulary-entries-section">
            <div className="section-title-row">
              <h3>단어 목록 ({entries.length})</h3>
              {!vocabulary.isBuiltin && (
                <button type="button" className="btn-secondary btn-sm" onClick={handleAddEntry}>
                  <span className="material-symbols-outlined">add</span>
                  용어 추가
                </button>
              )}
            </div>

            <div className="vocabulary-entries-container">
              <table className="vocabulary-entries-table">
                <thead>
                  <tr>
                    <th>단어/구문</th>
                    <th>발음 힌트 (Sounds Like)</th>
                    <th>표시할 텍스트 (Display As)</th>
                    {!vocabulary.isBuiltin && <th style={{ width: '48px' }}></th>}
                  </tr>
                </thead>
                <tbody>
                  {entries.length === 0 ? (
                    <tr>
                      <td colSpan={vocabulary.isBuiltin ? 3 : 4} className="text-center py-8 text-secondary">
                        등록된 용어가 없습니다.
                      </td>
                    </tr>
                  ) : (
                    entries.map((entry) => (
                      <tr key={entry.id}>
                        <td>
                          <input 
                            type="text" 
                            value={entry.phrase} 
                            onChange={(e) => handleUpdateEntry(entry.id, { phrase: e.target.value })}
                            disabled={vocabulary.isBuiltin}
                            placeholder="예: EC2"
                          />
                        </td>
                        <td>
                          <input 
                            type="text" 
                            value={entry.soundsLike || ''} 
                            onChange={(e) => handleUpdateEntry(entry.id, { soundsLike: e.target.value })}
                            disabled={vocabulary.isBuiltin}
                            placeholder="예: 이씨투"
                          />
                        </td>
                        <td>
                          <input 
                            type="text" 
                            value={entry.displayAs || ''} 
                            onChange={(e) => handleUpdateEntry(entry.id, { displayAs: e.target.value })}
                            disabled={vocabulary.isBuiltin}
                            placeholder="예: EC2"
                          />
                        </td>
                        {!vocabulary.isBuiltin && (
                          <td>
                            <button 
                              type="button" 
                              className="btn-icon danger" 
                              onClick={() => handleRemoveEntry(entry.id)}
                            >
                              <span className="material-symbols-outlined">delete</span>
                            </button>
                          </td>
                        )}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <div className="flex gap-2 mr-auto">
            <button 
              type="button" 
              className={`btn-secondary flex items-center gap-2 ${isSyncing ? 'btn-loading' : ''}`}
              onClick={handleSyncToAws}
              disabled={isSyncing}
            >
              {!isSyncing && <span className="material-symbols-outlined">sync</span>}
              {isSyncing ? '동기화 중...' : 'AWS에 동기화'}
            </button>
          </div>
          <button type="button" className="btn-primary px-8" onClick={onClose}>
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}
