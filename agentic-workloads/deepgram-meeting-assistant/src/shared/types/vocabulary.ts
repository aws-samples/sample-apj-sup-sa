import type { TranscribeLanguage } from './settings';

/**
 * AWS Transcribe Custom Vocabulary 동기화 상태
 */
export type VocabularyStatus = 'PENDING' | 'READY' | 'FAILED' | 'NOT_SYNCED';

/**
 * 용어집 언어 코드 (TranscribeLanguage와 호환)
 */
export type VocabularyLanguage = TranscribeLanguage;

/**
 * 용어집 메타데이터
 */
export interface Vocabulary {
  id: string;
  name: string;
  languageCode: VocabularyLanguage;
  isDefault: boolean;
  isBuiltin: boolean;
  awsVocabularyName: string | null;
  awsStatus: VocabularyStatus;
  lastSyncedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

/**
 * 용어집 항목
 */
export interface VocabularyEntry {
  id: string;
  vocabularyId: string;
  phrase: string;
  soundsLike: string | null;
  displayAs: string | null;
  createdAt: string;
}

/**
 * 용어집 생성 요청
 */
export interface CreateVocabularyRequest {
  name: string;
  languageCode: VocabularyLanguage;
}

/**
 * 용어집 업데이트 요청
 */
export interface UpdateVocabularyRequest {
  name?: string;
  languageCode?: VocabularyLanguage;
  isDefault?: boolean;
}

/**
 * 용어집 항목 생성 요청
 */
export interface CreateVocabularyEntryRequest {
  vocabularyId: string;
  phrase: string;
  soundsLike?: string;
  displayAs?: string;
}

/**
 * 용어집 항목 업데이트 요청
 */
export interface UpdateVocabularyEntryRequest {
  phrase?: string;
  soundsLike?: string | null;
  displayAs?: string | null;
}

/**
 * AWS 동기화 결과
 */
export interface VocabularySyncResult {
  success: boolean;
  awsVocabularyName?: string;
  error?: string;
}

/**
 * 기본 제공 용어집 정의 (앱 번들에 포함)
 */
export interface BuiltinVocabularyDefinition {
  name: string;
  languageCode: VocabularyLanguage;
  entries: Array<{
    phrase: string;
    soundsLike?: string;
    displayAs?: string;
  }>;
}
