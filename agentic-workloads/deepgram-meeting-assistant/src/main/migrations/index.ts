import type Database from 'better-sqlite3';
import { createLogger } from '../services/logger.service';

const log = createLogger('migrations');

interface Migration {
  version: number;
  name: string;
  up: (db: Database.Database) => void;
}

export const migrations: Migration[] = [
  {
    version: 1,
    name: 'initial_schema',
    up: (db) => {
      db.exec(`
        -- 스키마 버전 관리 (자동 마이그레이션용)
        CREATE TABLE IF NOT EXISTS schema_version (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- 미팅 테이블
        CREATE TABLE IF NOT EXISTS meetings (
          id TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          title TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'recording',
          language TEXT NOT NULL DEFAULT 'ko-KR',
          started_at TEXT NOT NULL,
          ended_at TEXT,
          duration INTEGER DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- 원본 전사 세그먼트 (Transcribe 결과 그대로, 화자 분리 포함)
        CREATE TABLE IF NOT EXISTS transcription_segments (
          id TEXT PRIMARY KEY,
          meeting_id TEXT NOT NULL,
          result_id TEXT NOT NULL,
          text TEXT NOT NULL,
          start_time REAL NOT NULL,
          end_time REAL NOT NULL,
          speaker_label TEXT,
          confidence REAL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        );

        -- 보정된 문장 (세그먼트와 독립적, 문장 단위)
        CREATE TABLE IF NOT EXISTS corrected_sentences (
          id TEXT PRIMARY KEY,
          meeting_id TEXT NOT NULL,
          original_text TEXT NOT NULL,
          corrected_text TEXT NOT NULL,
          start_time REAL NOT NULL,
          end_time REAL NOT NULL,
          speaker_label TEXT,
          model_id TEXT NOT NULL,
          corrected_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        );

        -- 미팅 요약 테이블
        CREATE TABLE IF NOT EXISTS meeting_summaries (
          id TEXT PRIMARY KEY,
          meeting_id TEXT NOT NULL UNIQUE,
          key_points TEXT NOT NULL,
          action_items TEXT NOT NULL,
          decisions TEXT NOT NULL,
          generated_at TEXT NOT NULL DEFAULT (datetime('now')),
          model_id TEXT NOT NULL,
          FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        );

        -- 인덱스
        CREATE INDEX IF NOT EXISTS idx_segments_meeting ON transcription_segments(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_segments_speaker ON transcription_segments(speaker_label);
        CREATE INDEX IF NOT EXISTS idx_corrected_meeting ON corrected_sentences(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_corrected_speaker ON corrected_sentences(speaker_label);
        CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
        CREATE INDEX IF NOT EXISTS idx_meetings_created ON meetings(created_at DESC);
      `);
    },
  },
  {
    version: 2,
    name: 'add_translated_text_to_corrected_sentences',
    up: (db) => {
      db.exec(`
        ALTER TABLE corrected_sentences ADD COLUMN translated_text TEXT;
      `);
    },
  },
  {
    version: 3,
    name: 'add_segment_ids_to_corrected_sentences',
    up: (db) => {
      db.exec(`
        ALTER TABLE corrected_sentences ADD COLUMN segment_ids TEXT;
      `);
    },
  },
  {
    version: 4,
    name: 'update_meeting_summaries_schema',
    up: (db) => {
      db.exec(`
        -- Drop old table and create new one with updated schema
        DROP TABLE IF EXISTS meeting_summaries;
        
        CREATE TABLE meeting_summaries (
          id TEXT PRIMARY KEY,
          meeting_id TEXT NOT NULL UNIQUE,
          main_topics TEXT NOT NULL DEFAULT '[]',
          topic_discussions TEXT NOT NULL DEFAULT '[]',
          key_takeaways TEXT NOT NULL DEFAULT '[]',
          confirmed_actions TEXT NOT NULL DEFAULT '[]',
          pending_actions TEXT NOT NULL DEFAULT '[]',
          follow_ups TEXT NOT NULL DEFAULT '[]',
          open_issues TEXT NOT NULL DEFAULT '[]',
          generated_at TEXT NOT NULL DEFAULT (datetime('now')),
          model_id TEXT NOT NULL,
          FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        );
      `);
    },
  },
  {
    version: 5,
    name: 'add_metadata_to_meetings',
    up: (db) => {
      db.exec(`
        -- 미팅 타입별 메타 정보를 저장하는 JSON 컬럼 추가
        ALTER TABLE meetings ADD COLUMN metadata TEXT DEFAULT '{}';
        
        -- 미팅 타입별 빠른 조회를 위한 인덱스
        CREATE INDEX IF NOT EXISTS idx_meetings_type ON meetings(type);
      `);
    },
  },
  {
    version: 6,
    name: 'add_conversation_logs_table',
    up: (db) => {
      db.exec(`
        -- 대화 로그 테이블 (전사 내용을 주제별로 분절해 정리)
        CREATE TABLE IF NOT EXISTS conversation_logs (
          id TEXT PRIMARY KEY,
          meeting_id TEXT NOT NULL UNIQUE,
          topics TEXT NOT NULL DEFAULT '[]',
          generated_at TEXT NOT NULL DEFAULT (datetime('now')),
          model_id TEXT NOT NULL,
          FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        );
        
        -- 대화 로그 조회를 위한 인덱스
        CREATE INDEX IF NOT EXISTS idx_conversation_logs_meeting 
          ON conversation_logs(meeting_id);
      `);
    },
  },
  {
    version: 7,
    name: 'add_vocabulary_tables',
    up: (db) => {
      db.exec(`
        -- 용어집 테이블 (AWS Transcribe Custom Vocabulary 관리)
        CREATE TABLE IF NOT EXISTS vocabularies (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          language_code TEXT NOT NULL,
          is_default INTEGER NOT NULL DEFAULT 0,
          is_builtin INTEGER NOT NULL DEFAULT 0,
          aws_vocabulary_name TEXT,
          aws_status TEXT NOT NULL DEFAULT 'NOT_SYNCED',
          last_synced_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        
        -- 용어집 항목 테이블
        CREATE TABLE IF NOT EXISTS vocabulary_entries (
          id TEXT PRIMARY KEY,
          vocabulary_id TEXT NOT NULL,
          phrase TEXT NOT NULL,
          sounds_like TEXT,
          display_as TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (vocabulary_id) REFERENCES vocabularies(id) ON DELETE CASCADE
        );
        
        -- 인덱스
        CREATE INDEX IF NOT EXISTS idx_vocabularies_language 
          ON vocabularies(language_code);
        CREATE INDEX IF NOT EXISTS idx_vocabularies_default 
          ON vocabularies(is_default);
        CREATE INDEX IF NOT EXISTS idx_vocabulary_entries_vocabulary 
          ON vocabulary_entries(vocabulary_id);
      `);
    },
  },
  {
    version: 8,
    name: 'add_unique_meeting_result_to_segments',
    up: (db) => {
      // 중복 세그먼트를 삭제하기 전에, 삭제될 세그먼트 id -> survivor(MAX(rowid)) id 매핑을 만든다.
      // survivor는 가장 나중에 들어온 행(=MAX rowid)으로, saveSegment의 ON CONFLICT latest-wins 의미와 일치한다.
      // 그래야 corrected_sentences.segment_ids가 곧 삭제될 id를 참조하더라도 survivor로 remap 할 수 있다.
      type DupRow = { id: string; survivor_id: string };
      const dupRows = db.prepare(`
        SELECT s.id AS id, m.survivor_id AS survivor_id
        FROM transcription_segments s
        JOIN (
          SELECT meeting_id, result_id,
                 (SELECT id FROM transcription_segments t
                  WHERE t.meeting_id = ts.meeting_id AND t.result_id = ts.result_id
                  ORDER BY t.rowid DESC LIMIT 1) AS survivor_id
          FROM transcription_segments ts
          GROUP BY meeting_id, result_id
          HAVING COUNT(*) > 1
        ) m ON s.meeting_id = m.meeting_id AND s.result_id = m.result_id
      `).all() as DupRow[];

      // non-survivor id -> survivor id 매핑 (survivor는 자기 자신이라 별도 등록 불필요)
      const oldToSurvivor = new Map<string, string>();
      for (const row of dupRows) {
        if (row.id !== row.survivor_id) {
          oldToSurvivor.set(row.id, row.survivor_id);
        }
      }

      if (oldToSurvivor.size > 0) {
        // segment_ids에 삭제 대상 id가 들어 있는 보정 문장만 골라 remap 한다.
        type CsRow = { id: string; segment_ids: string | null };
        const csRows = db.prepare(
          `SELECT id, segment_ids FROM corrected_sentences WHERE segment_ids IS NOT NULL`
        ).all() as CsRow[];
        const updateStmt = db.prepare(
          `UPDATE corrected_sentences SET segment_ids = ? WHERE id = ?`
        );

        for (const cs of csRows) {
          if (!cs.segment_ids) continue;
          let parsed: unknown;
          try {
            parsed = JSON.parse(cs.segment_ids);
          } catch {
            // 구버전의 잘못된 JSON은 건드리지 않는다.
            continue;
          }
          if (!Array.isArray(parsed)) continue;

          // 매핑이 필요한 id가 하나도 없으면 건너뛴다.
          const needsRemap = parsed.some(
            (idVal) => typeof idVal === 'string' && oldToSurvivor.has(idVal)
          );
          if (!needsRemap) continue;

          // 각 id를 survivor로 치환하고, 첫 등장 순서를 유지하며 중복을 제거한다.
          const remapped: string[] = [];
          const seen = new Set<string>();
          for (const idVal of parsed) {
            if (typeof idVal !== 'string') continue;
            const target = oldToSurvivor.get(idVal) ?? idVal;
            if (!seen.has(target)) {
              seen.add(target);
              remapped.push(target);
            }
          }
          updateStmt.run(JSON.stringify(remapped), cs.id);
        }
      }

      // 기존 중복 (meeting_id, result_id) 행이 있으면 가장 나중에 들어온 것(MAX rowid)만 남기고 정리
      db.exec(`
        DELETE FROM transcription_segments
        WHERE rowid NOT IN (
          SELECT MAX(rowid) FROM transcription_segments
          GROUP BY meeting_id, result_id
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_meeting_result
          ON transcription_segments(meeting_id, result_id);
      `);
    },
  },
];

function getCurrentVersion(db: Database.Database): number {
  try {
    const stmt = db.prepare(
      'SELECT MAX(version) as version FROM schema_version'
    );
    const row = stmt.get() as { version: number | null } | undefined;
    return row?.version ?? 0;
  } catch {
    // Table doesn't exist yet
    return 0;
  }
}

function setVersion(db: Database.Database, version: number): void {
  const stmt = db.prepare(
    'INSERT INTO schema_version (version, applied_at) VALUES (?, datetime(\'now\'))'
  );
  stmt.run(version);
}

export function runMigrations(db: Database.Database): void {
  // Ensure schema_version table exists first
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_version (
      version INTEGER PRIMARY KEY,
      applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);

  const currentVersion = getCurrentVersion(db);

  // 다운그레이드 가드: DB가 이 바이너리가 아는 것보다 더 최신 버전이면 무조건 실패시킨다.
  // 그냥 열면 구버전 코드가 신버전 스키마(예: v8 UNIQUE 인덱스)와 충돌해 데이터가 손상될 수 있다.
  const latestSupportedVersion = migrations.reduce((max, m) => Math.max(max, m.version), 0);
  if (currentVersion > latestSupportedVersion) {
    log.error(
      { currentVersion, latestSupportedVersion },
      'Database schema is newer than supported'
    );
    throw new Error(
      `Database schema version ${currentVersion} is newer than this app supports (max ${latestSupportedVersion}). ` +
        'This usually means the database was created by a newer version of the app. ' +
        'Please update the app; downgrading is not supported.'
    );
  }

  for (const migration of migrations) {
    if (migration.version > currentVersion) {
      log.info({ version: migration.version, name: migration.name }, 'Running migration');
      db.transaction(() => {
        migration.up(db);
        setVersion(db, migration.version);
      })();
      log.info({ version: migration.version }, 'Migration completed');
    }
  }
}
