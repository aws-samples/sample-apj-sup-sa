/**
 * Vocabulary IPC Handlers
 *
 * 용어집 관련 IPC 핸들러를 등록합니다.
 */

import { ipcMain } from 'electron';
import { IPC_CHANNELS } from '@shared/constants/ipc-channels';
import { getVocabularyService } from '../services/vocabulary.service';
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
} from '@shared/types/vocabulary';
import { createLogger } from '../services/logger.service';

const log = createLogger('vocabulary-ipc');

export function registerVocabularyHandlers(): void {
  const service = getVocabularyService();

  // ==========================================================================
  // Vocabulary CRUD
  // ==========================================================================

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_LIST,
    (_event, languageCode?: VocabularyLanguage): Vocabulary[] => {
      try {
        if (languageCode) {
          return service.listVocabulariesByLanguage(languageCode);
        }
        return service.listVocabularies();
      } catch (error) {
        log.error({ err: error }, 'Failed to list vocabularies');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_GET,
    (_event, id: string): Vocabulary | null => {
      try {
        return service.getVocabulary(id);
      } catch (error) {
        log.error({ err: error, id }, 'Failed to get vocabulary');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_CREATE,
    (_event, request: CreateVocabularyRequest): Vocabulary => {
      try {
        return service.createVocabulary(request);
      } catch (error) {
        log.error({ err: error, request }, 'Failed to create vocabulary');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_UPDATE,
    (_event, id: string, updates: UpdateVocabularyRequest): Vocabulary | null => {
      try {
        return service.updateVocabulary(id, updates);
      } catch (error) {
        log.error({ err: error, id, updates }, 'Failed to update vocabulary');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_DELETE,
    (_event, id: string): boolean => {
      try {
        return service.deleteVocabulary(id);
      } catch (error) {
        log.error({ err: error, id }, 'Failed to delete vocabulary');
        throw error;
      }
    }
  );

  // ==========================================================================
  // Vocabulary Entries
  // ==========================================================================

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_ENTRY_LIST,
    (_event, vocabularyId: string): VocabularyEntry[] => {
      try {
        return service.getEntries(vocabularyId);
      } catch (error) {
        log.error({ err: error, vocabularyId }, 'Failed to list entries');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_ENTRY_ADD,
    (_event, request: CreateVocabularyEntryRequest): VocabularyEntry => {
      try {
        return service.addEntry(request);
      } catch (error) {
        log.error({ err: error, request }, 'Failed to add entry');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_ENTRY_UPDATE,
    (_event, entryId: string, updates: UpdateVocabularyEntryRequest): VocabularyEntry | null => {
      try {
        return service.updateEntry(entryId, updates);
      } catch (error) {
        log.error({ err: error, entryId, updates }, 'Failed to update entry');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_ENTRY_REMOVE,
    (_event, entryId: string): boolean => {
      try {
        return service.removeEntry(entryId);
      } catch (error) {
        log.error({ err: error, entryId }, 'Failed to remove entry');
        throw error;
      }
    }
  );

  // ==========================================================================
  // Default Vocabulary
  // ==========================================================================

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_SET_DEFAULT,
    (_event, vocabularyId: string, languageCode: VocabularyLanguage): void => {
      try {
        service.setDefaultVocabulary(vocabularyId, languageCode);
      } catch (error) {
        log.error({ err: error, vocabularyId, languageCode }, 'Failed to set default vocabulary');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_GET_DEFAULT,
    (_event, languageCode: VocabularyLanguage): Vocabulary | null => {
      try {
        return service.getDefaultVocabulary(languageCode);
      } catch (error) {
        log.error({ err: error, languageCode }, 'Failed to get default vocabulary');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_CLEAR_DEFAULT,
    (_event, languageCode: VocabularyLanguage): void => {
      try {
        service.clearDefaultVocabulary(languageCode);
      } catch (error) {
        log.error({ err: error, languageCode }, 'Failed to clear default vocabulary');
        throw error;
      }
    }
  );

  // ==========================================================================
  // AWS Sync
  // ==========================================================================

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_SYNC_TO_AWS,
    async (_event, vocabularyId: string): Promise<VocabularySyncResult> => {
      try {
        return await service.syncToAws(vocabularyId);
      } catch (error) {
        log.error({ err: error, vocabularyId }, 'Failed to sync to AWS');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_CHECK_STATUS,
    async (_event, vocabularyId: string): Promise<VocabularyStatus> => {
      try {
        return await service.checkAwsStatus(vocabularyId);
      } catch (error) {
        log.error({ err: error, vocabularyId }, 'Failed to check AWS status');
        throw error;
      }
    }
  );

  ipcMain.handle(
    IPC_CHANNELS.VOCABULARY_GENERATE_FILE,
    (_event, vocabularyId: string): string => {
      try {
        return service.generateVocabularyFile(vocabularyId);
      } catch (error) {
        log.error({ err: error, vocabularyId }, 'Failed to generate vocabulary file');
        throw error;
      }
    }
  );

  log.info('Vocabulary IPC handlers registered');
}
