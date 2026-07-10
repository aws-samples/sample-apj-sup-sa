import { describe, it, expect, beforeEach, vi } from 'vitest';
import Database from 'better-sqlite3';
import { runMigrations, migrations } from '../index';

// electron을 모킹 (logger.service가 app.isPackaged를 읽음). better-sqlite3는 모킹하지 않음.
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn().mockReturnValue('/tmp'),
    getVersion: vi.fn().mockReturnValue('1.0.0'),
    isPackaged: false,
  },
}));

// saveSegment와 동일한 멱등 SQL (Step 4에서 database.service.ts에 반영하는 것과 일치해야 함)
const SAVE_SEGMENT_SQL = `
  INSERT INTO transcription_segments
  (id, meeting_id, result_id, text, start_time, end_time, speaker_label, confidence, created_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  ON CONFLICT(meeting_id, result_id) DO UPDATE SET
    id = excluded.id,
    text = excluded.text,
    start_time = excluded.start_time,
    end_time = excluded.end_time,
    speaker_label = excluded.speaker_label,
    confidence = excluded.confidence
`;

describe('migrations + segment idempotency (real sqlite)', () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(':memory:');
    db.pragma('foreign_keys = ON');
    runMigrations(db);
    db.prepare(`
      INSERT INTO meetings (id, type, title, status, language, started_at, duration, created_at, updated_at)
      VALUES ('m1', 'agentic', 't', 'recording', 'ko-KR', '2026-01-01', 0, '2026-01-01', '2026-01-01')
    `).run();
  });

  it('creates a UNIQUE index on (meeting_id, result_id)', () => {
    const idx = db.prepare(`SELECT name FROM sqlite_master WHERE type='index' AND name='idx_segments_meeting_result'`).get();
    expect(idx).toBeDefined();
  });

  it('keeps only one row when same (meeting_id, result_id) inserted twice via ON CONFLICT', () => {
    const ins = db.prepare(SAVE_SEGMENT_SQL);
    ins.run('row-1', 'm1', 'r-1', 'hello', 0, 1, null, null, '2026-01-01');
    ins.run('row-2', 'm1', 'r-1', 'hello world', 0, 1.5, null, null, '2026-01-01');
    const rows = db.prepare(`SELECT * FROM transcription_segments WHERE meeting_id='m1'`).all() as any[];
    expect(rows).toHaveLength(1);
    expect(rows[0].text).toBe('hello world');
    expect(rows[0].id).toBe('row-2');
  });

  const ROW_IDS_BY_RESULT_SQL = `SELECT id FROM transcription_segments WHERE meeting_id = ? AND result_id = ?`;

  it('returns row id(s) for a given (meeting_id, result_id)', () => {
    db.prepare(SAVE_SEGMENT_SQL).run('row-abc', 'm1', 'r-1', 'hi', 0, 1, null, null, '2026-01-01');
    const rows = db.prepare(ROW_IDS_BY_RESULT_SQL).all('m1', 'r-1') as Array<{ id: string }>;
    expect(rows.map((r) => r.id)).toEqual(['row-abc']);
  });

  it('returns empty when no segment matches', () => {
    const rows = db.prepare(ROW_IDS_BY_RESULT_SQL).all('m1', 'nope') as Array<{ id: string }>;
    expect(rows).toHaveLength(0);
  });

  it('keeps the latest (MAX rowid) segment as survivor and remaps corrected_sentences.segment_ids to it', () => {
    // 구버전 DB 상태를 흉내내기 위해 v8 유니크 인덱스를 먼저 제거한다.
    db.exec('DROP INDEX IF EXISTS idx_segments_meeting_result');

    // 같은 (meeting_id, result_id)를 가진 중복 세그먼트 2개 삽입.
    // 'seg-old'가 먼저, 'seg-new'가 나중에 들어간다. ON CONFLICT의 latest-wins 의미에 맞춰
    // 가장 나중에(=MAX rowid) 들어온 'seg-new'가 survivor가 되고, 'seg-old'는 삭제 대상이 된다.
    const insSeg = db.prepare(`
      INSERT INTO transcription_segments
      (id, meeting_id, result_id, text, start_time, end_time, speaker_label, confidence, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    insSeg.run('seg-old', 'm1', 'r-1', 'hello', 0, 1, null, 0.5, '2026-01-01');
    insSeg.run('seg-new', 'm1', 'r-1', 'hello world', 0, 1.5, null, 0.9, '2026-01-01');

    // 삭제 대상(non-survivor)인 'seg-old'를 참조하는 보정 문장 삽입.
    db.prepare(`
      INSERT INTO corrected_sentences
      (id, meeting_id, original_text, corrected_text, start_time, end_time, speaker_label, model_id, corrected_at, segment_ids)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run('cs-1', 'm1', 'hello world', 'Hello, world.', 0, 1.5, null, 'model-x', '2026-01-01', '["seg-old"]');

    // v8 마이그레이션의 up을 직접 실행 (수동으로 만든 중복 상태에 대해 실제 작업 수행).
    const v8 = migrations.find((m) => m.version === 8)!;
    db.transaction(() => v8.up(db))();

    // (a) survivor 한 행만 남아야 하고, 그것은 가장 나중에 들어온 'seg-new'여야 한다.
    //     그 내용(text/timing/confidence)이 보존되어야 한다 (latest = canonical).
    const segRows = db.prepare(
      `SELECT id, text, start_time, end_time, confidence FROM transcription_segments WHERE meeting_id='m1' AND result_id='r-1'`
    ).all() as any[];
    expect(segRows).toHaveLength(1);
    expect(segRows[0].id).toBe('seg-new');
    expect(segRows[0].text).toBe('hello world');
    expect(segRows[0].start_time).toBe(0);
    expect(segRows[0].end_time).toBe(1.5);
    expect(segRows[0].confidence).toBe(0.9);

    // (b) 보정 문장의 segment_ids가 survivor('seg-new')로 remap 되어야 한다.
    const cs = db.prepare(`SELECT segment_ids FROM corrected_sentences WHERE id='cs-1'`).get() as { segment_ids: string };
    expect(JSON.parse(cs.segment_ids)).toEqual(['seg-new']);
  });

  it('does not throw when re-running on an up-to-date DB (idempotent)', () => {
    // beforeEach가 이미 최신 버전까지 마이그레이션했으므로 재실행은 no-op이어야 한다.
    expect(() => runMigrations(db)).not.toThrow();
  });

  it('throws when the DB schema version is newer than this app supports (downgrade guard)', () => {
    // 미래 버전 DB를 흉내내기 위해 현재 바이너리가 모르는 더 높은 schema_version 행을 삽입한다.
    db.prepare("INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'))").run(999);
    expect(() => runMigrations(db)).toThrow(/newer than this app supports/);
  });
});
