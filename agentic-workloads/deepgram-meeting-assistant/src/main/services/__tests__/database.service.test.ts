import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { DatabaseService } from '../database.service';
import { app } from 'electron';
import fs from 'fs';
import path from 'path';
import os from 'os';

// Mock electron
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(),
    getVersion: vi.fn().mockReturnValue('1.0.0'),
    isPackaged: false,
  },
}));

// Mock better-sqlite3
const mockPrepare = vi.fn();
const mockRun = vi.fn();
const mockGet = vi.fn();
const mockAll = vi.fn();
const mockExec = vi.fn();
const mockPragma = vi.fn();
const mockTransaction = vi.fn((fn) => fn); // Execute immediately
const mockClose = vi.fn();

vi.mock('better-sqlite3', () => {
  return {
    default: class {
      prepare = mockPrepare;
      exec = mockExec;
      pragma = mockPragma;
      transaction = mockTransaction;
      close = mockClose;
    },
  };
});

describe('DatabaseService', () => {
  let service: DatabaseService;
  let tempDir: string;

  beforeEach(() => {
    // Reset mocks
    vi.clearAllMocks();
    
    // Setup default mock behaviors
    mockPrepare.mockReturnValue({
      run: mockRun,
      get: mockGet,
      all: mockAll,
    });
    mockRun.mockReturnValue({ changes: 1, lastInsertRowid: 1 });
    // better-sqlite3의 .all()은 항상 배열을 반환하므로 기본값을 빈 배열로 둔다 (마이그레이션 v8이 .all()을 사용함)
    mockAll.mockReturnValue([]);

    // Mock specific DB calls that happen in constructor/migrations
    mockTransaction.mockImplementation((fn) => () => fn()); 
    // The migration script calls transaction() which returns a function that needs to be called
    
    // Create a temp directory for each test
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'meeting-assistant-test-'));
    vi.mocked(app.getPath).mockReturnValue(tempDir);

    service = new DatabaseService();
  });

  afterEach(() => {
    try {
      service.close();
    } catch (e) {
      // Ignore
    }
    try {
      fs.rmSync(tempDir, { recursive: true, force: true });
    } catch (e) {
      // Ignore
    }
  });

  describe('Metadata Parsing (JSON)', () => {
    it('creates meeting with empty metadata if none provided', () => {
      // Setup mock for INSERT
      service.createMeeting('weekly', 'ko-KR');
      
      // Verify INSERT called with empty JSON object
      expect(mockPrepare).toHaveBeenCalledWith(expect.stringContaining('INSERT INTO meetings'));
      expect(mockRun).toHaveBeenCalledWith(
        expect.any(String), // id
        'weekly',
        expect.any(String), // title
        'recording',
        'ko-KR',
        expect.any(String), // started_at
        0,
        '{}', // metadata defaults to empty object string
        expect.any(String),
        expect.any(String)
      );
    });

    it('creates meeting with provided metadata', () => {
      const metadata = { company: 'ACME' };
      service.createMeeting('client', 'ko-KR', undefined, metadata);
      
      expect(mockRun).toHaveBeenCalledWith(
        expect.any(String),
        'client',
        expect.any(String),
        'recording',
        'ko-KR',
        expect.any(String),
        0,
        JSON.stringify(metadata),
        expect.any(String),
        expect.any(String)
      );
    });

    it('updates metadata with shallow merge', () => {
      // Mock existing metadata fetch
      mockGet.mockReturnValueOnce({ metadata: JSON.stringify({ company: 'ACME', note: 'Seoul' }) });

      const updateSuccess = service.updateMeetingMetadata('test-id', { note: 'Busan' });
      
      expect(updateSuccess).toBe(true);
      
      // Verify UPDATE called with merged JSON
      expect(mockPrepare).toHaveBeenCalledWith(expect.stringContaining('UPDATE meetings SET metadata = ?'));
      expect(mockRun).toHaveBeenCalledWith(
        JSON.stringify({ company: 'ACME', note: 'Busan' }),
        expect.any(String),
        'test-id'
      );
    });

    it('handles malformed JSON in database gracefully during read', () => {
      // Mock getMeeting query result
      const meetingRow = {
        id: 'test-id',
        type: 'weekly',
        title: 'Meeting',
        status: 'recording',
        language: 'ko-KR',
        started_at: new Date().toISOString(),
        duration: 0,
        metadata: '{invalid-json', // Corrupt data
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString()
      };

      // We need to mock sequential calls to get()
      // 1. getMeeting -> returns meetingRow
      // 2. getSummaryByMeeting -> returns undefined (no summary)
      mockGet
        .mockReturnValueOnce(meetingRow)
        .mockReturnValueOnce(undefined);

      // We can mock prepare to return different objects based on query?
      // Or just mock `all` to return empty array (for segments/sentences)
      mockAll.mockReturnValue([]);

      const retrieved = service.getMeeting('test-id');

      // Should fallback to empty object
      expect(retrieved?.metadata).toEqual({});
    });
  });

  describe('getSegmentInfoByResultId', () => {
    it('aggregates startTime as min and endTime as max across segments', () => {
      // 한 resultId에 여러 segment가 매핑된 경우 (시간 역순으로 들어와도)
      mockAll.mockReturnValueOnce([
        { id: 's2', start_time: 12.5, end_time: 15.0, speaker_label: 'Speaker 1' },
        { id: 's1', start_time: 10.0, end_time: 11.0, speaker_label: 'Speaker 0' },
      ]);

      const info = service.getSegmentInfoByResultId('m1', 'm1:abc:3');

      expect(info.ids).toEqual(['s2', 's1']);
      expect(info.startTime).toBe(10.0); // min
      expect(info.endTime).toBe(15.0); // max
      // 대표 speaker는 쿼리가 ORDER BY start_time이므로 mock 배열 [0]을 사용
      expect(info.speakerLabel).toBe('Speaker 1');
    });

    it('returns zeroed safe defaults when no segment matches (orphan)', () => {
      mockAll.mockReturnValueOnce([]);

      const info = service.getSegmentInfoByResultId('m1', 'missing');

      expect(info.ids).toEqual([]);
      expect(info.startTime).toBe(0);
      expect(info.endTime).toBe(0);
      expect(info.speakerLabel).toBeNull();
    });
  });
});