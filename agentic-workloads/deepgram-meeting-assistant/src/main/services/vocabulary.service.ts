/**
 * Vocabulary Service
 *
 * AWS Transcribe Custom Vocabulary 관리를 담당하는 서비스입니다.
 * 용어집 CRUD, 기본 용어집 관리, AWS 동기화 기능을 제공합니다.
 */

import Database from 'better-sqlite3';
import { app } from 'electron';
import path from 'path';
import { v4 as uuidv4 } from 'uuid';
import {
  S3Client,
  PutObjectCommand,
  HeadBucketCommand,
  CreateBucketCommand,
  type BucketLocationConstraint,
} from '@aws-sdk/client-s3';
import {
  TranscribeClient,
  CreateVocabularyCommand,
  UpdateVocabularyCommand,
  GetVocabularyCommand,
} from '@aws-sdk/client-transcribe';
import { createLogger } from './logger.service';
import { runMigrations } from '../migrations';
import { settingsService } from './settings.service';
import type { AWSCredentials as SettingsAWSCredentials } from './settings.service';
import type {
  Vocabulary,
  VocabularyEntry,
  VocabularyStatus,
  VocabularyLanguage,
  CreateVocabularyRequest,
  UpdateVocabularyRequest,
  CreateVocabularyEntryRequest,
  UpdateVocabularyEntryRequest,
  VocabularySyncResult,
  BuiltinVocabularyDefinition,
} from '@shared/types/vocabulary';
import {
  BUILTIN_VOCABULARIES,
  BUILTIN_VOCABULARY_ID_PREFIX,
} from '@shared/constants/default-vocabularies';

const log = createLogger('vocabulary');

const VOCABULARY_BUCKET_PREFIX = 'meeting-assistant-vocabularies';
const VOCABULARY_OBJECT_PREFIX = 'meeting-assistant/vocabularies';
const VOCABULARY_NAME_PREFIX = 'meeting-assistant';
const MAX_VOCABULARY_FILE_BYTES = 50 * 1024;

// ============================================================================
// DB Row Types
// ============================================================================

interface VocabularyRow {
  id: string;
  name: string;
  language_code: string;
  is_default: number;
  is_builtin: number;
  aws_vocabulary_name: string | null;
  aws_status: string;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

interface VocabularyEntryRow {
  id: string;
  vocabulary_id: string;
  phrase: string;
  sounds_like: string | null;
  display_as: string | null;
  created_at: string;
}

// ============================================================================
// VocabularyService
// ============================================================================

export class VocabularyService {
  private db: Database.Database;

  constructor() {
    const dbPath = path.join(app.getPath('userData'), 'meetings.db');
    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('foreign_keys = ON');
    runMigrations(this.db);

    // 기본 제공 용어집 초기화
    this.ensureBuiltinVocabularies();
  }

  // ==========================================================================
  // Vocabulary CRUD
  // ==========================================================================

  /**
   * 새 용어집을 생성합니다.
   */
  createVocabulary(request: CreateVocabularyRequest): Vocabulary {
    const id = uuidv4();
    const now = new Date().toISOString();

    const stmt = this.db.prepare(`
      INSERT INTO vocabularies (id, name, language_code, is_default, is_builtin, aws_status, created_at, updated_at)
      VALUES (?, ?, ?, 0, 0, 'NOT_SYNCED', ?, ?)
    `);

    stmt.run(id, request.name, request.languageCode, now, now);
    log.info({ id, name: request.name }, 'Vocabulary created');

    return {
      id,
      name: request.name,
      languageCode: request.languageCode,
      isDefault: false,
      isBuiltin: false,
      awsVocabularyName: null,
      awsStatus: 'NOT_SYNCED',
      lastSyncedAt: null,
      createdAt: now,
      updatedAt: now,
    };
  }

  /**
   * 용어집을 조회합니다.
   */
  getVocabulary(id: string): Vocabulary | null {
    const stmt = this.db.prepare('SELECT * FROM vocabularies WHERE id = ?');
    const row = stmt.get(id) as VocabularyRow | undefined;
    if (!row) return null;
    return this.rowToVocabulary(row);
  }

  /**
   * 모든 용어집을 조회합니다.
   */
  listVocabularies(): Vocabulary[] {
    const stmt = this.db.prepare(
      'SELECT * FROM vocabularies ORDER BY is_builtin DESC, created_at ASC'
    );
    const rows = stmt.all() as VocabularyRow[];
    return rows.map((row) => this.rowToVocabulary(row));
  }

  /**
   * 특정 언어의 용어집만 조회합니다.
   */
  listVocabulariesByLanguage(languageCode: VocabularyLanguage): Vocabulary[] {
    const stmt = this.db.prepare(
      'SELECT * FROM vocabularies WHERE language_code = ? ORDER BY is_builtin DESC, created_at ASC'
    );
    const rows = stmt.all(languageCode) as VocabularyRow[];
    return rows.map((row) => this.rowToVocabulary(row));
  }

  /**
   * 용어집을 업데이트합니다.
   * builtin 용어집의 이름/언어는 변경할 수 없습니다.
   */
  updateVocabulary(id: string, updates: UpdateVocabularyRequest): Vocabulary | null {
    const existing = this.getVocabulary(id);
    if (!existing) return null;

    // builtin 용어집은 이름/언어 변경 불가
    if (existing.isBuiltin && (updates.name || updates.languageCode)) {
      log.warn({ id }, 'Cannot modify builtin vocabulary name or language');
      throw new Error('기본 제공 용어집의 이름이나 언어는 변경할 수 없습니다.');
    }

    const now = new Date().toISOString();
    const setClauses: string[] = ['updated_at = ?'];
    const values: (string | number | null)[] = [now];
    const isLanguageChanged =
      updates.languageCode !== undefined && updates.languageCode !== existing.languageCode;

    if (updates.name !== undefined) {
      setClauses.push('name = ?');
      values.push(updates.name);
    }
    if (updates.languageCode !== undefined) {
      setClauses.push('language_code = ?');
      values.push(updates.languageCode);
    }

    if (isLanguageChanged) {
      // 언어가 바뀌면 기존 AWS 용어집과의 매핑은 무효화해야 합니다.
      setClauses.push('aws_vocabulary_name = ?');
      values.push(null);
      setClauses.push('aws_status = ?');
      values.push('NOT_SYNCED');
      setClauses.push('last_synced_at = ?');
      values.push(null);
    }

    values.push(id);

    const stmt = this.db.prepare(
      `UPDATE vocabularies SET ${setClauses.join(', ')} WHERE id = ?`
    );
    stmt.run(...values);

    return this.getVocabulary(id);
  }

  /**
   * 용어집을 삭제합니다.
   * builtin 용어집은 삭제할 수 없습니다.
   */
  deleteVocabulary(id: string): boolean {
    const existing = this.getVocabulary(id);
    if (!existing) return false;

    if (existing.isBuiltin) {
      log.warn({ id }, 'Cannot delete builtin vocabulary');
      throw new Error('기본 제공 용어집은 삭제할 수 없습니다.');
    }

    const stmt = this.db.prepare('DELETE FROM vocabularies WHERE id = ?');
    const result = stmt.run(id);
    log.info({ id }, 'Vocabulary deleted');
    return result.changes > 0;
  }

  // ==========================================================================
  // Vocabulary Entry Management
  // ==========================================================================

  /**
   * 용어집에 항목을 추가합니다.
   */
  addEntry(request: CreateVocabularyEntryRequest): VocabularyEntry {
    const vocabulary = this.getVocabulary(request.vocabularyId);
    if (!vocabulary) {
      throw new Error('용어집을 찾을 수 없습니다.');
    }

    // builtin 용어집은 항목 추가 불가
    if (vocabulary.isBuiltin) {
      throw new Error('기본 제공 용어집에는 항목을 추가할 수 없습니다.');
    }

    const id = uuidv4();
    const now = new Date().toISOString();

    const stmt = this.db.prepare(`
      INSERT INTO vocabulary_entries (id, vocabulary_id, phrase, sounds_like, display_as, created_at)
      VALUES (?, ?, ?, ?, ?, ?)
    `);

    stmt.run(
      id,
      request.vocabularyId,
      request.phrase,
      request.soundsLike ?? null,
      request.displayAs ?? null,
      now
    );

    // 용어집 updated_at 갱신
    this.touchVocabulary(request.vocabularyId);

    return {
      id,
      vocabularyId: request.vocabularyId,
      phrase: request.phrase,
      soundsLike: request.soundsLike ?? null,
      displayAs: request.displayAs ?? null,
      createdAt: now,
    };
  }

  /**
   * 용어집 항목을 업데이트합니다.
   */
  updateEntry(entryId: string, updates: UpdateVocabularyEntryRequest): VocabularyEntry | null {
    const existing = this.getEntry(entryId);
    if (!existing) return null;

    const vocabulary = this.getVocabulary(existing.vocabularyId);
    if (vocabulary?.isBuiltin) {
      throw new Error('기본 제공 용어집의 항목은 수정할 수 없습니다.');
    }

    const setClauses: string[] = [];
    const values: (string | null)[] = [];

    if (updates.phrase !== undefined) {
      setClauses.push('phrase = ?');
      values.push(updates.phrase);
    }
    if (updates.soundsLike !== undefined) {
      setClauses.push('sounds_like = ?');
      values.push(updates.soundsLike);
    }
    if (updates.displayAs !== undefined) {
      setClauses.push('display_as = ?');
      values.push(updates.displayAs);
    }

    if (setClauses.length === 0) return existing;

    values.push(entryId);

    const stmt = this.db.prepare(
      `UPDATE vocabulary_entries SET ${setClauses.join(', ')} WHERE id = ?`
    );
    stmt.run(...values);

    // 용어집 updated_at 갱신
    this.touchVocabulary(existing.vocabularyId);

    return this.getEntry(entryId);
  }

  /**
   * 용어집 항목을 삭제합니다.
   */
  removeEntry(entryId: string): boolean {
    const existing = this.getEntry(entryId);
    if (!existing) return false;

    const vocabulary = this.getVocabulary(existing.vocabularyId);
    if (vocabulary?.isBuiltin) {
      throw new Error('기본 제공 용어집의 항목은 삭제할 수 없습니다.');
    }

    const stmt = this.db.prepare('DELETE FROM vocabulary_entries WHERE id = ?');
    const result = stmt.run(entryId);

    // 용어집 updated_at 갱신
    this.touchVocabulary(existing.vocabularyId);

    return result.changes > 0;
  }

  /**
   * 단일 항목을 조회합니다.
   */
  getEntry(entryId: string): VocabularyEntry | null {
    const stmt = this.db.prepare('SELECT * FROM vocabulary_entries WHERE id = ?');
    const row = stmt.get(entryId) as VocabularyEntryRow | undefined;
    if (!row) return null;
    return this.rowToEntry(row);
  }

  /**
   * 용어집의 모든 항목을 조회합니다.
   */
  getEntries(vocabularyId: string): VocabularyEntry[] {
    const stmt = this.db.prepare(
      'SELECT * FROM vocabulary_entries WHERE vocabulary_id = ? ORDER BY phrase'
    );
    const rows = stmt.all(vocabularyId) as VocabularyEntryRow[];
    return rows.map((row) => this.rowToEntry(row));
  }

  /**
   * 용어집의 항목 수를 반환합니다.
   */
  getEntryCount(vocabularyId: string): number {
    const stmt = this.db.prepare(
      'SELECT COUNT(*) as count FROM vocabulary_entries WHERE vocabulary_id = ?'
    );
    const row = stmt.get(vocabularyId) as { count: number };
    return row.count;
  }

  // ==========================================================================
  // Default Vocabulary Management
  // ==========================================================================

  /**
   * 특정 언어의 기본 용어집을 설정합니다.
   * 해당 언어의 다른 용어집은 기본 해제됩니다.
   */
  setDefaultVocabulary(vocabularyId: string, languageCode: VocabularyLanguage): void {
    const vocabulary = this.getVocabulary(vocabularyId);
    if (!vocabulary) {
      throw new Error('용어집을 찾을 수 없습니다.');
    }
    if (vocabulary.languageCode !== languageCode) {
      throw new Error('용어집 언어와 설정 언어가 일치하지 않습니다.');
    }

    const now = new Date().toISOString();

    // 트랜잭션으로 처리
    this.db.transaction(() => {
      // 해당 언어의 모든 용어집 기본 해제
      const resetStmt = this.db.prepare(
        'UPDATE vocabularies SET is_default = 0, updated_at = ? WHERE language_code = ?'
      );
      resetStmt.run(now, languageCode);

      // 선택한 용어집을 기본으로 설정
      const setStmt = this.db.prepare(
        'UPDATE vocabularies SET is_default = 1, updated_at = ? WHERE id = ?'
      );
      setStmt.run(now, vocabularyId);
    })();

    log.info({ vocabularyId, languageCode }, 'Default vocabulary set');
  }

  /**
   * 기본 용어집 설정을 해제합니다.
   */
  clearDefaultVocabulary(languageCode: VocabularyLanguage): void {
    const now = new Date().toISOString();
    const stmt = this.db.prepare(
      'UPDATE vocabularies SET is_default = 0, updated_at = ? WHERE language_code = ?'
    );
    stmt.run(now, languageCode);
    log.info({ languageCode }, 'Default vocabulary cleared');
  }

  /**
   * 특정 언어의 기본 용어집을 조회합니다.
   */
  getDefaultVocabulary(languageCode: VocabularyLanguage): Vocabulary | null {
    const stmt = this.db.prepare(
      'SELECT * FROM vocabularies WHERE language_code = ? AND is_default = 1 LIMIT 1'
    );
    const row = stmt.get(languageCode) as VocabularyRow | undefined;
    if (!row) return null;
    return this.rowToVocabulary(row);
  }

  // ==========================================================================
  // AWS Sync
  // ==========================================================================

  /**
   * 용어집을 AWS Transcribe Table Format으로 변환합니다.
   * 형식: Phrase\tSoundsLike\tDisplayAs (탭 구분)
   */
  generateVocabularyFile(vocabularyId: string): string {
    const entries = this.getEntries(vocabularyId);
    // AWS Transcribe requires 4 columns: Phrase, SoundsLike, IPA, DisplayAs
    const lines = ['Phrase\tSoundsLike\tIPA\tDisplayAs'];

    for (const entry of entries) {
      const soundsLike = entry.soundsLike ?? '';
      const displayAs = entry.displayAs ?? '';
      // IPA column is empty but must be present
      lines.push(`${entry.phrase}\t${soundsLike}\t\t${displayAs}`);
    }

    return lines.join('\n');
  }

  /**
   * 용어집을 AWS에 동기화합니다.
   * S3 업로드 후 Create/UpdateVocabulary를 호출합니다.
   */
  async syncToAws(vocabularyId: string): Promise<VocabularySyncResult> {
    const vocabulary = this.getVocabulary(vocabularyId);
    if (!vocabulary) {
      return { success: false, error: '용어집을 찾을 수 없습니다.' };
    }

    const entries = this.getEntries(vocabularyId);
    if (entries.length === 0) {
      return { success: false, error: '동기화할 용어가 없습니다.' };
    }

    const fileContent = this.generateVocabularyFile(vocabularyId);
    const fileSize = Buffer.byteLength(fileContent, 'utf-8');
    if (fileSize > MAX_VOCABULARY_FILE_BYTES) {
      return { success: false, error: '용어집 파일이 50KB를 초과했습니다.' };
    }

    const now = new Date().toISOString();

    try {
      const credentials = await this.requireCredentials();
      const s3Client = this.createS3Client(credentials);
      const transcribeClient = this.createTranscribeClient(credentials);

      const bucketName = this.getBucketName(credentials.region, credentials.accessKeyId);
      const objectKey = this.getVocabularyObjectKey(vocabularyId);
      const vocabularyName = this.getAwsVocabularyName(vocabularyId);
      const fileUri = `s3://${bucketName}/${objectKey}`;

      await this.ensureBucketExists(s3Client, bucketName, credentials.region);
      await s3Client.send(
        new PutObjectCommand({
          Bucket: bucketName,
          Key: objectKey,
          Body: Buffer.from(fileContent, 'utf-8'),
          ContentType: 'text/plain; charset=utf-8',
        })
      );

      const createCommand = new CreateVocabularyCommand({
        LanguageCode: vocabulary.languageCode,
        VocabularyName: vocabularyName,
        VocabularyFileUri: fileUri,
      });

      try {
        await transcribeClient.send(createCommand);
      } catch (error) {
        if (this.isVocabularyAlreadyExists(error)) {
          const updateCommand = new UpdateVocabularyCommand({
            LanguageCode: vocabulary.languageCode,
            VocabularyName: vocabularyName,
            VocabularyFileUri: fileUri,
          });
          await transcribeClient.send(updateCommand);
        } else {
          throw error;
        }
      }

      this.updateVocabularySyncState(vocabularyId, {
        awsVocabularyName: vocabularyName,
        awsStatus: 'PENDING',
        lastSyncedAt: now,
      });

      log.info({ vocabularyId, vocabularyName }, 'Vocabulary synced to AWS');

      return {
        success: true,
        awsVocabularyName: vocabularyName,
      };
    } catch (error) {
      const errorMessage = this.formatAwsError(error);
      this.updateVocabularySyncState(vocabularyId, {
        awsStatus: 'FAILED',
        lastSyncedAt: now,
      });
      log.error({ err: error, vocabularyId }, 'Failed to sync vocabulary to AWS');
      return { success: false, error: errorMessage };
    }
  }

  /**
   * AWS 용어집 상태를 확인합니다.
   */
  async checkAwsStatus(vocabularyId: string): Promise<VocabularyStatus> {
    const vocabulary = this.getVocabulary(vocabularyId);
    if (!vocabulary || !vocabulary.awsVocabularyName) {
      return 'NOT_SYNCED';
    }

    try {
      const credentials = await this.requireCredentials();
      const transcribeClient = this.createTranscribeClient(credentials);
      const command = new GetVocabularyCommand({
        VocabularyName: vocabulary.awsVocabularyName,
      });
      const response = await transcribeClient.send(command);
      const state = response.VocabularyState ?? 'FAILED';
      const status = this.mapAwsVocabularyState(state);
      const now = new Date().toISOString();

      this.updateVocabularySyncState(vocabularyId, {
        awsStatus: status,
        lastSyncedAt: now,
      });

      if (status === 'FAILED' && response.FailureReason) {
        log.warn(
          { vocabularyId, reason: response.FailureReason },
          'Vocabulary sync failed'
        );
      }

      return status;
    } catch (error) {
      log.error({ err: error, vocabularyId }, 'Failed to check vocabulary status');
      return vocabulary.awsStatus;
    }
  }

  /**
   * AWS에 동기화된 용어집 이름을 안전하게 반환합니다.
   * - 로컬 언어 코드와 기대 언어가 다르면 사용하지 않음
   * - AWS 실제 LanguageCode가 기대 언어와 다르면 사용하지 않음
   * - AWS 상태가 READY가 아니면 사용하지 않음
   */
  async resolveUsableAwsVocabularyName(
    vocabularyId: string,
    expectedLanguageCode: VocabularyLanguage
  ): Promise<string | null> {
    const vocabulary = this.getVocabulary(vocabularyId);
    if (!vocabulary || !vocabulary.awsVocabularyName || vocabulary.awsStatus !== 'READY') {
      return null;
    }

    if (vocabulary.languageCode !== expectedLanguageCode) {
      return null;
    }

    try {
      const credentials = await this.requireCredentials();
      const transcribeClient = this.createTranscribeClient(credentials);
      const response = await transcribeClient.send(
        new GetVocabularyCommand({ VocabularyName: vocabulary.awsVocabularyName })
      );

      const awsStatus = this.mapAwsVocabularyState(response.VocabularyState ?? 'FAILED');
      const awsLanguage = response.LanguageCode as VocabularyLanguage | undefined;
      const now = new Date().toISOString();

      this.updateVocabularySyncState(vocabularyId, {
        awsStatus,
        lastSyncedAt: now,
      });

      if (awsStatus !== 'READY') {
        return null;
      }

      if (awsLanguage && awsLanguage !== expectedLanguageCode) {
        log.warn(
          {
            vocabularyId,
            vocabularyName: vocabulary.awsVocabularyName,
            expectedLanguageCode,
            awsLanguageCode: awsLanguage,
          },
          'AWS vocabulary language mismatch detected, skipping vocabulary'
        );
        return null;
      }

      return vocabulary.awsVocabularyName;
    } catch (error) {
      log.warn({ err: error, vocabularyId }, 'Failed to validate AWS vocabulary language');
      return null;
    }
  }

  // ========================================================================== 
  // Builtin Vocabulary Management
  // ==========================================================================

  /**
   * 기본 제공 용어집이 없으면 생성합니다.
   * 앱 시작 시 자동 호출됩니다.
   */
  ensureBuiltinVocabularies(): void {
    for (const definition of BUILTIN_VOCABULARIES) {
      const builtinId = `${BUILTIN_VOCABULARY_ID_PREFIX}${definition.languageCode}`;
      const now = new Date().toISOString();
      const existing = this.getVocabulary(builtinId);

      if (!existing) {
        const hasDefault = this.getDefaultVocabulary(definition.languageCode);
        const isDefault = hasDefault ? 0 : 1;

        const insertVocab = this.db.prepare(`
          INSERT INTO vocabularies (id, name, language_code, is_default, is_builtin, aws_status, created_at, updated_at)
          VALUES (?, ?, ?, ?, 1, 'NOT_SYNCED', ?, ?)
        `);
        insertVocab.run(
          builtinId,
          definition.name,
          definition.languageCode,
          isDefault,
          now,
          now
        );

        const insertEntry = this.db.prepare(`
          INSERT INTO vocabulary_entries (id, vocabulary_id, phrase, sounds_like, display_as, created_at)
          VALUES (?, ?, ?, ?, ?, ?)
        `);

        for (const entry of definition.entries) {
          const entryId = uuidv4();
          insertEntry.run(
            entryId,
            builtinId,
            entry.phrase,
            entry.soundsLike ?? null,
            entry.displayAs ?? null,
            now
          );
        }

        log.info(
          { id: builtinId, name: definition.name, entryCount: definition.entries.length },
          'Builtin vocabulary created'
        );
        continue;
      }

      const updateVocab = this.db.prepare(`
        UPDATE vocabularies
        SET name = ?, language_code = ?, is_builtin = 1, updated_at = ?
        WHERE id = ?
      `);
      updateVocab.run(definition.name, definition.languageCode, now, builtinId);

      const existingEntries = this.getEntries(builtinId);
      if (!this.builtinEntriesMatch(existingEntries, definition.entries)) {
        const deleteEntries = this.db.prepare(
          'DELETE FROM vocabulary_entries WHERE vocabulary_id = ?'
        );
        deleteEntries.run(builtinId);

        const insertEntry = this.db.prepare(`
          INSERT INTO vocabulary_entries (id, vocabulary_id, phrase, sounds_like, display_as, created_at)
          VALUES (?, ?, ?, ?, ?, ?)
        `);

        for (const entry of definition.entries) {
          const entryId = uuidv4();
          insertEntry.run(
            entryId,
            builtinId,
            entry.phrase,
            entry.soundsLike ?? null,
            entry.displayAs ?? null,
            now
          );
        }

        const resetSync = this.db.prepare(`
          UPDATE vocabularies
          SET aws_status = 'NOT_SYNCED', last_synced_at = NULL, updated_at = ?
          WHERE id = ?
        `);
        resetSync.run(now, builtinId);

        log.info(
          { id: builtinId, name: definition.name, entryCount: definition.entries.length },
          'Builtin vocabulary updated'
        );
      } else {
        log.debug({ id: builtinId }, 'Builtin vocabulary already up to date');
      }
    }
  }

  // ==========================================================================
  // Private Helpers
  // ==========================================================================

  private builtinEntriesMatch(
    existingEntries: VocabularyEntry[],
    definitionEntries: BuiltinVocabularyDefinition['entries']
  ): boolean {
    if (existingEntries.length !== definitionEntries.length) return false;

    const toKey = (entry: {
      phrase: string;
      soundsLike?: string | null;
      displayAs?: string | null;
    }): string =>
      `${entry.phrase}\t${entry.soundsLike ?? ''}\t${entry.displayAs ?? ''}`;

    const existingKeys = new Set(existingEntries.map((entry) => toKey(entry)));
    if (existingKeys.size !== existingEntries.length) return false;

    for (const entry of definitionEntries) {
      if (!existingKeys.has(toKey(entry))) return false;
    }

    return true;
  }

  private async requireCredentials(): Promise<SettingsAWSCredentials> {
    const credentials = await settingsService.getCredentials();
    if (!credentials) {
      throw new Error('AWS credentials not configured');
    }
    return credentials;
  }

  private createS3Client(credentials: SettingsAWSCredentials): S3Client {
    return new S3Client({
      region: credentials.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
      },
    });
  }

  private createTranscribeClient(credentials: SettingsAWSCredentials): TranscribeClient {
    return new TranscribeClient({
      region: credentials.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
      },
    });
  }

  private getBucketName(region: string, accessKeyId: string): string {
    const safeKey = accessKeyId.toLowerCase().replace(/[^a-z0-9]/g, '');
    const suffix = safeKey.slice(-12) || 'user';
    const name = `${VOCABULARY_BUCKET_PREFIX}-${region}-${suffix}`;
    return name.slice(0, 63);
  }

  private getVocabularyObjectKey(vocabularyId: string): string {
    return `${VOCABULARY_OBJECT_PREFIX}/${vocabularyId}.txt`;
  }

  private getAwsVocabularyName(vocabularyId: string): string {
    return `${VOCABULARY_NAME_PREFIX}-${vocabularyId}`;
  }

  private async ensureBucketExists(
    client: S3Client,
    bucketName: string,
    region: string
  ): Promise<void> {
    try {
      await client.send(new HeadBucketCommand({ Bucket: bucketName }));
      return;
    } catch (error) {
      if (!this.isBucketNotFound(error)) {
        throw error;
      }
    }

    const config = region === 'us-east-1'
      ? undefined
      : { LocationConstraint: region as BucketLocationConstraint };

    await client.send(
      new CreateBucketCommand({
        Bucket: bucketName,
        CreateBucketConfiguration: config,
      })
    );
  }

  private isBucketNotFound(error: unknown): boolean {
    if (!error || typeof error !== 'object') return false;
    const name = (error as { name?: string }).name;
    const status = (error as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode;
    return name === 'NotFound' || name === 'NoSuchBucket' || status === 404;
  }

  private isVocabularyAlreadyExists(error: unknown): boolean {
    if (!error || typeof error !== 'object') return false;
    const name = (error as { name?: string }).name;
    return name === 'ConflictException' || name === 'ResourceExistsException';
  }

  private mapAwsVocabularyState(state: string): VocabularyStatus {
    switch (state) {
      case 'READY':
        return 'READY';
      case 'PENDING':
        return 'PENDING';
      case 'FAILED':
        return 'FAILED';
      default:
        return 'FAILED';
    }
  }

  private updateVocabularySyncState(
    vocabularyId: string,
    updates: {
      awsVocabularyName?: string | null;
      awsStatus?: VocabularyStatus;
      lastSyncedAt?: string | null;
    }
  ): void {
    const setClauses: string[] = [];
    const values: Array<string | null> = [];

    if (updates.awsVocabularyName !== undefined) {
      setClauses.push('aws_vocabulary_name = ?');
      values.push(updates.awsVocabularyName);
    }
    if (updates.awsStatus !== undefined) {
      setClauses.push('aws_status = ?');
      values.push(updates.awsStatus);
    }
    if (updates.lastSyncedAt !== undefined) {
      setClauses.push('last_synced_at = ?');
      values.push(updates.lastSyncedAt);
    }

    if (setClauses.length === 0) return;

    setClauses.push('updated_at = ?');
    values.push(new Date().toISOString());
    values.push(vocabularyId);

    const stmt = this.db.prepare(
      `UPDATE vocabularies SET ${setClauses.join(', ')} WHERE id = ?`
    );
    stmt.run(...values);
  }

  private formatAwsError(error: unknown): string {
    if (error instanceof Error && error.message) {
      return error.message;
    }
    if (error && typeof error === 'object') {
      const maybeName = (error as { name?: unknown }).name;
      const maybeMessage = (error as { message?: unknown }).message;
      const metadata = (error as { $metadata?: { httpStatusCode?: number; requestId?: string } }).$metadata;
      const parts = [
        typeof maybeName === 'string' ? maybeName : null,
        typeof maybeMessage === 'string' ? maybeMessage : null,
        metadata?.httpStatusCode ? `HTTP ${metadata.httpStatusCode}` : null,
        metadata?.requestId ? `Request ${metadata.requestId}` : null,
      ].filter(Boolean);
      if (parts.length > 0) {
        return parts.join(' - ');
      }
    }
    return 'AWS 요청 중 오류가 발생했습니다.';
  }

  private rowToVocabulary(row: VocabularyRow): Vocabulary {
    return {
      id: row.id,
      name: row.name,
      languageCode: row.language_code as VocabularyLanguage,
      isDefault: row.is_default === 1,
      isBuiltin: row.is_builtin === 1,
      awsVocabularyName: row.aws_vocabulary_name,
      awsStatus: row.aws_status as VocabularyStatus,
      lastSyncedAt: row.last_synced_at,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    };
  }

  private rowToEntry(row: VocabularyEntryRow): VocabularyEntry {
    return {
      id: row.id,
      vocabularyId: row.vocabulary_id,
      phrase: row.phrase,
      soundsLike: row.sounds_like,
      displayAs: row.display_as,
      createdAt: row.created_at,
    };
  }

  private touchVocabulary(vocabularyId: string): void {
    const now = new Date().toISOString();
    const stmt = this.db.prepare(
      'UPDATE vocabularies SET updated_at = ? WHERE id = ?'
    );
    stmt.run(now, vocabularyId);
  }

  close(): void {
    this.db.close();
  }
}

// Singleton instance
let vocabularyServiceInstance: VocabularyService | null = null;

export function getVocabularyService(): VocabularyService {
  if (!vocabularyServiceInstance) {
    vocabularyServiceInstance = new VocabularyService();
  }
  return vocabularyServiceInstance;
}
