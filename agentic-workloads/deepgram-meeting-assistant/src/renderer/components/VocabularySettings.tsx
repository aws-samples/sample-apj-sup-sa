import { useState, useEffect, useCallback } from 'react';
import { Vocabulary, VocabularyLanguage } from '../../shared/types/vocabulary';

interface VocabularySettingsProps {
  onEditVocabulary: (vocabularyId: string) => void;
}

const LANGUAGES: { value: VocabularyLanguage; label: string }[] = [
  { value: 'ko-KR', label: '한국어 (ko-KR)' },
  { value: 'en-US', label: '영어 (en-US)' },
];

function VocabularySettings({ onEditVocabulary }: VocabularySettingsProps) {
  const [vocabularies, setVocabularies] = useState<Vocabulary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [selectedLanguage, setSelectedLanguage] = useState<VocabularyLanguage>('ko-KR');
  const [vocabularyEntriesCount, setVocabularyEntriesCount] = useState<Record<string, number>>({});

  const fetchVocabularies = useCallback(async () => {
    if (!window.electronAPI) return;
    setIsLoading(true);
    try {
      const list = await window.electronAPI.vocabulary.list();
      setVocabularies(list);

      // 항목 수 조회
      const counts: Record<string, number> = {};
      await Promise.all(
        list.map(async (v) => {
          const entries = await window.electronAPI.vocabulary.listEntries(v.id);
          counts[v.id] = entries.length;
        })
      );
      setVocabularyEntriesCount(counts);
    } catch (error) {
      console.error('Failed to fetch vocabularies:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchVocabularies();
  }, [fetchVocabularies]);

  const handleCreateVocabulary = async () => {
    if (!window.electronAPI) return;
    const name = prompt('새 용어집의 이름을 입력하세요:');
    if (!name) return;

    setIsCreating(true);
    try {
      await window.electronAPI.vocabulary.create({
        name,
        languageCode: selectedLanguage,
      });
      await fetchVocabularies();
    } catch (error) {
      console.error('Failed to create vocabulary:', error);
      alert('용어집 생성에 실패했습니다.');
    } finally {
      setIsCreating(false);
    }
  };

  const handleDeleteVocabulary = async (id: string, name: string) => {
    if (!window.electronAPI) return;
    if (!confirm(`'${name}' 용어집을 삭제하시겠습니까?`)) return;

    try {
      const success = await window.electronAPI.vocabulary.delete(id);
      if (success) {
        await fetchVocabularies();
      } else {
        alert('용어집 삭제에 실패했습니다.');
      }
    } catch (error) {
      console.error('Failed to delete vocabulary:', error);
      alert('용어집 삭제 중 오류가 발생했습니다.');
    }
  };

  const handleSetDefault = async (languageCode: VocabularyLanguage, vocabularyId: string) => {
    if (!window.electronAPI) return;
    try {
      if (vocabularyId === 'none') {
        await window.electronAPI.vocabulary.clearDefault(languageCode);
      } else {
        await window.electronAPI.vocabulary.setDefault(vocabularyId, languageCode);
      }
      await fetchVocabularies();
    } catch (error) {
      console.error('Failed to set default vocabulary:', error);
      alert('기본 용어집 설정에 실패했습니다.');
    }
  };

  const filteredVocabularies = vocabularies.filter(v => v.languageCode === selectedLanguage);
  const defaultVocab = filteredVocabularies.find(v => v.isDefault);

  return (
    <div className="settings-card">
      <div className="settings-card-header">
        <h2>용어집 관리</h2>
        <p>AWS Transcribe 인식률 향상을 위한 사용자 지정 용어 관리</p>
      </div>

      <div className="settings-form">
        <div className="form-row">
          <div className="form-field">
            <label htmlFor="language-select">언어 선택</label>
            <select 
              id="language-select"
              value={selectedLanguage} 
              onChange={(e) => setSelectedLanguage(e.target.value as VocabularyLanguage)}
            >
              {LANGUAGES.map((lang) => (
                <option key={lang.value} value={lang.value}>
                  {lang.label}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="default-vocab-select">기본 용어집</label>
            <select
              id="default-vocab-select"
              value={defaultVocab?.id || 'none'}
              onChange={(e) => handleSetDefault(selectedLanguage, e.target.value)}
            >
              <option value="none">사용 안 함</option>
              {filteredVocabularies.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="vocabulary-list">
          {isLoading ? (
            <div className="vocabulary-loading">불러오는 중...</div>
          ) : filteredVocabularies.length === 0 ? (
            <div className="vocabulary-empty">등록된 용어집이 없습니다.</div>
          ) : (
            filteredVocabularies.map((v) => (
              <div key={v.id} className={`vocabulary-item ${v.isDefault ? 'is-default' : ''}`}>
                <div className="vocabulary-item-info">
                  <div className="vocabulary-item-header">
                    <span className="material-symbols-outlined vocabulary-icon">
                      {v.isBuiltin ? 'auto_stories' : 'book'}
                    </span>
                    <span className="vocabulary-name">{v.name}</span>
                    {v.isBuiltin && <span className="vocabulary-badge builtin">기본 제공</span>}
                    <span className={`vocabulary-status ${v.awsStatus}`}>
                      {v.awsStatus === 'READY' && '● 동기화됨'}
                      {v.awsStatus === 'PENDING' && '● 동기화 진행 중'}
                      {v.awsStatus === 'FAILED' && '● 동기화 실패'}
                      {v.awsStatus === 'NOT_SYNCED' && '○ 동기화 필요'}
                    </span>
                  </div>
                  <div className="vocabulary-item-meta">
                    <span className="vocabulary-count">{vocabularyEntriesCount[v.id] || 0}개 용어</span>
                    <div className="vocabulary-actions">
                      <button 
                        type="button"
                        className="btn-icon" 
                        onClick={() => onEditVocabulary(v.id)}
                        title="편집"
                      >
                        <span className="material-symbols-outlined">edit</span>
                        보기
                      </button>
                      {!v.isBuiltin && (
                        <button 
                          type="button"
                          className="btn-icon danger" 
                          onClick={() => handleDeleteVocabulary(v.id, v.name)}
                          title="삭제"
                        >
                          <span className="material-symbols-outlined">delete</span>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        <button 
          type="button"
          className="btn-secondary" 
          onClick={handleCreateVocabulary}
          disabled={isCreating}
          style={{ width: 'fit-content' }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '18px', marginRight: '4px' }}>add</span>
          새 용어집 만들기
        </button>
      </div>
    </div>
  );
}

export default VocabularySettings;
