/**
 * Database Service
 * 
 * SQLite 데이터베이스 CRUD 작업을 담당하는 서비스입니다.
 * 
 * ORCH-005, ORCH-017: JSON Parsing Without Validation → zod 스키마 검증 추가
 */

import Database from 'better-sqlite3';
import { app } from 'electron';
import path from 'path';
import { v4 as uuidv4 } from 'uuid';
import { z } from 'zod';
import { createLogger } from './logger.service';
import type {
  Meeting,
  MeetingDetail,
  MeetingStatus,
  MeetingType,
  MeetingMetadata,
  MeetingSummary,
  ConversationLog,
  ConversationTopic,
} from '../../shared/types/meeting';
import type { TranscribeLanguage } from '../../shared/types/settings';
import type { TranscriptionSegment, CorrectedSentence } from '../../shared/types/transcription';
import type {
  MeetingRow,
  TranscriptionSegmentRow,
  CorrectedSentenceRow,
  MeetingSummaryRow,
  ConversationLogRow,
} from '../../shared/types/database';
import { runMigrations } from '../migrations';

// ============================================================================
// Zod Schemas for JSON Validation
// ============================================================================

/**
 * Metadata 스키마 - 유연한 record 타입
 */
const MeetingMetadataSchema = z.record(z.string(), z.unknown()).catch({});

/**
 * Segment IDs 스키마 - string 배열
 */
const SegmentIdsSchema = z.array(z.string()).catch([]);

/**
 * ActionItem 스키마
 */
// export: Post-Meeting Agent의 회의록 수정 도구가 필드 검증에 재사용한다.
export const ActionItemSchema = z.object({
  task: z.string(),
  owner: z.string(),
  deadline: z.string(),
});

/**
 * TopicDiscussion 스키마
 */
// export: Post-Meeting Agent의 회의록 수정 도구가 필드 검증에 재사용한다.
export const TopicDiscussionSchema = z.object({
  topic: z.string(),
  discussions: z.array(z.string()),
  decisions: z.array(z.string()),
});

/**
 * Summary 관련 스키마들
 */
const MainTopicsSchema = z.array(z.string()).catch([]);
const TopicDiscussionsSchema = z.array(TopicDiscussionSchema).catch([]);
const KeyTakeawaysSchema = z.array(z.string()).catch([]);
const ActionsSchema = z.array(ActionItemSchema).catch([]);
const FollowUpsSchema = z.array(z.string()).catch([]);
const OpenIssuesSchema = z.array(z.string()).catch([]);

/**
 * ConversationTopic 스키마
 */
// export: Post-Meeting Agent의 회의록 수정 도구가 필드 검증에 재사용한다.
export const ConversationTopicSchema = z.object({
  title: z.string(),
  points: z.array(z.string()),
});
const ConversationTopicsSchema = z.array(ConversationTopicSchema).catch([]);

const log = createLogger('database');

export class DatabaseService {
  private db: Database.Database;

  constructor() {
    const dbPath = path.join(app.getPath('userData'), 'meetings.db');
    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('foreign_keys = ON');
    runMigrations(this.db);
  }

  createMeeting(
    type: MeetingType,
    language: TranscribeLanguage,
    title?: string,
    metadata?: MeetingMetadata
  ): Meeting {
    const id = uuidv4();
    const now = new Date();
    const meetingTitle = title || `Meeting ${now.toLocaleDateString()} ${now.toLocaleTimeString()}`;
    const metadataJson = JSON.stringify(metadata ?? {});

    const stmt = this.db.prepare(`
      INSERT INTO meetings (id, type, title, status, language, started_at, duration, metadata, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    stmt.run(
      id,
      type,
      meetingTitle,
      'recording',
      language,
      now.toISOString(),
      0,
      metadataJson,
      now.toISOString(),
      now.toISOString()
    );

    return {
      id,
      type,
      title: meetingTitle,
      status: 'recording',
      language,
      startedAt: now,
      duration: 0,
      metadata: metadata ?? {},
      createdAt: now,
      updatedAt: now,
    };
  }

  getMeeting(id: string): MeetingDetail | null {
    const meetingStmt = this.db.prepare('SELECT * FROM meetings WHERE id = ?');
    const row = meetingStmt.get(id) as MeetingRow | undefined;

    if (!row) return null;

    const segments = this.getSegmentsByMeeting(id);
    const correctedSentences = this.getCorrectedSentencesByMeeting(id);
    const summary = this.getSummaryByMeeting(id);
    const conversationLog = this.getConversationLogByMeeting(id);

    return {
      ...this.rowToMeeting(row),
      segments,
      correctedSentences,
      summary: summary || undefined,
      conversationLog: conversationLog || undefined,
    };
  }

  updateMeetingStatus(id: string, status: MeetingStatus, endedAt?: Date): void {
    const now = new Date();
    const stmt = this.db.prepare(`
      UPDATE meetings 
      SET status = ?, ended_at = ?, updated_at = ?
      WHERE id = ?
    `);
    stmt.run(status, endedAt?.toISOString() || null, now.toISOString(), id);
  }

  updateMeetingTitle(id: string, title: string): void {
    const now = new Date();
    const stmt = this.db.prepare(`
      UPDATE meetings SET title = ?, updated_at = ? WHERE id = ?
    `);
    stmt.run(title, now.toISOString(), id);
  }

  updateMeetingDuration(id: string, duration: number): void {
    const now = new Date();
    const stmt = this.db.prepare(`
      UPDATE meetings SET duration = ?, updated_at = ? WHERE id = ?
    `);
    stmt.run(duration, now.toISOString(), id);
  }

  /**
   * 미팅 메타데이터를 업데이트합니다.
   * 기존 메타데이터와 병합하여 저장합니다 (shallow merge).
   * @returns 업데이트 성공 여부 (미팅이 존재하고 변경된 경우 true)
   */
  updateMeetingMetadata(id: string, metadata: MeetingMetadata): boolean {
    const now = new Date();
    
    // 기존 메타데이터 조회
    const existingMetadata = this.getMeetingMetadata(id);
    if (existingMetadata === null) {
      // 미팅이 존재하지 않음
      return false;
    }
    
    // 기존 메타데이터와 병합 (shallow merge)
    const mergedMetadata = { ...existingMetadata, ...metadata };
    
    const stmt = this.db.prepare(`
      UPDATE meetings SET metadata = ?, updated_at = ? WHERE id = ?
    `);
    const result = stmt.run(JSON.stringify(mergedMetadata), now.toISOString(), id);
    
    return result.changes > 0;
  }

  /**
   * 미팅이 존재하는지 확인합니다.
   */
  meetingExists(id: string): boolean {
    const stmt = this.db.prepare('SELECT 1 FROM meetings WHERE id = ? LIMIT 1');
    const row = stmt.get(id);
    return row !== undefined;
  }

  getMeetingMetadata(id: string): MeetingMetadata | null {
    const stmt = this.db.prepare('SELECT metadata FROM meetings WHERE id = ?');
    const row = stmt.get(id) as { metadata: string } | undefined;
    if (!row) return null;
    return this.parseMetadata(row.metadata);
  }

  listMeetings(limit = 50, offset = 0): Meeting[] {
    const stmt = this.db.prepare(`
      SELECT * FROM meetings ORDER BY created_at DESC LIMIT ? OFFSET ?
    `);
    const rows = stmt.all(limit, offset) as MeetingRow[];
    return rows.map((row) => this.rowToMeeting(row));
  }

  deleteMeeting(id: string): void {
    const stmt = this.db.prepare('DELETE FROM meetings WHERE id = ?');
    stmt.run(id);
  }

  /**
   * 모든 미팅 데이터를 삭제합니다.
   * 주의: 이 작업은 되돌릴 수 없습니다.
   */
  deleteAllMeetings(): number {
    // CASCADE로 인해 관련된 segments, corrected_sentences, summaries도 자동 삭제됨
    const stmt = this.db.prepare('DELETE FROM meetings');
    const result = stmt.run();
    return result.changes;
  }

  saveSegment(segment: Omit<TranscriptionSegment, 'createdAt'>): void {
    const stmt = this.db.prepare(`
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
    `);
    stmt.run(
      segment.id,
      segment.meetingId,
      segment.resultId,
      segment.text,
      segment.startTime,
      segment.endTime,
      segment.speakerLabel,
      segment.confidence || null,
      new Date().toISOString()
    );
  }

  getSegmentsByMeeting(meetingId: string): TranscriptionSegment[] {
    const stmt = this.db.prepare(`
      SELECT * FROM transcription_segments WHERE meeting_id = ? ORDER BY start_time
    `);
    const rows = stmt.all(meetingId) as TranscriptionSegmentRow[];
    return rows.map((row) => this.rowToSegment(row));
  }

  getSegmentRowIdsByResultId(meetingId: string, resultId: string): string[] {
    const stmt = this.db.prepare(`
      SELECT id FROM transcription_segments
      WHERE meeting_id = ? AND result_id = ?
    `);
    const rows = stmt.all(meetingId, resultId) as Array<{ id: string }>;
    return rows.map((r) => r.id);
  }

  /**
   * resultId에 매핑되는 segment들의 row id와 시간/speaker 메타를 함께 반환합니다.
   * 교정 문장 저장 시 원본 segment와 동일한 시간축(startTime)을 부여해 렌더러의
   * 시간순 정렬이 교정/미교정 문장 간에 어긋나지 않도록 하기 위함입니다.
   * 한 resultId에 여러 segment가 있을 수 있어 startTime은 최소값, endTime은
   * 최대값을 취하고(첫 행만 보는 LIMIT 1보다 정확), speaker는 가장 이른 segment 것을 쓴다.
   */
  getSegmentInfoByResultId(
    meetingId: string,
    resultId: string
  ): { ids: string[]; startTime: number; endTime: number; speakerLabel: string | null } {
    const stmt = this.db.prepare(`
      SELECT id, start_time, end_time, speaker_label FROM transcription_segments
      WHERE meeting_id = ? AND result_id = ?
      ORDER BY start_time
    `);
    const rows = stmt.all(meetingId, resultId) as Array<{
      id: string;
      start_time: number;
      end_time: number;
      speaker_label: string | null;
    }>;
    if (rows.length === 0) {
      return { ids: [], startTime: 0, endTime: 0, speakerLabel: null };
    }
    const startTime = Math.min(...rows.map((r) => r.start_time));
    const endTime = Math.max(...rows.map((r) => r.end_time));
    const speakerLabel = rows[0].speaker_label ?? null;
    return { ids: rows.map((r) => r.id), startTime, endTime, speakerLabel };
  }

  saveCorrectedSentence(sentence: Omit<CorrectedSentence, 'correctedAt'>): void {
    const stmt = this.db.prepare(`
      INSERT INTO corrected_sentences
      (id, meeting_id, original_text, corrected_text, translated_text, segment_ids, start_time, end_time, speaker_label, model_id, corrected_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    stmt.run(
      sentence.id,
      sentence.meetingId,
      sentence.originalText,
      sentence.correctedText,
      sentence.translatedText ?? null,
      JSON.stringify(sentence.segmentIds ?? []),
      sentence.startTime,
      sentence.endTime,
      sentence.speakerLabel,
      sentence.modelId,
      new Date().toISOString()
    );
  }

  getCorrectedSentencesByMeeting(meetingId: string): CorrectedSentence[] {
    const stmt = this.db.prepare(`
      SELECT * FROM corrected_sentences WHERE meeting_id = ? ORDER BY start_time
    `);
    const rows = stmt.all(meetingId) as CorrectedSentenceRow[];
    return rows.map((row) => this.rowToCorrectedSentence(row));
  }

  saveSummary(summary: Omit<MeetingSummary, 'generatedAt'>): void {
    const stmt = this.db.prepare(`
      INSERT OR REPLACE INTO meeting_summaries
      (id, meeting_id, main_topics, topic_discussions, key_takeaways, confirmed_actions, pending_actions, follow_ups, open_issues, model_id, generated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    stmt.run(
      summary.id,
      summary.meetingId,
      JSON.stringify(summary.mainTopics),
      JSON.stringify(summary.topicDiscussions),
      JSON.stringify(summary.keyTakeaways),
      JSON.stringify(summary.confirmedActions),
      JSON.stringify(summary.pendingActions),
      JSON.stringify(summary.followUps),
      JSON.stringify(summary.openIssues),
      summary.modelId,
      new Date().toISOString()
    );
  }

  getSummaryByMeeting(meetingId: string): MeetingSummary | null {
    const stmt = this.db.prepare('SELECT * FROM meeting_summaries WHERE meeting_id = ?');
    const row = stmt.get(meetingId) as MeetingSummaryRow | undefined;
    if (!row) return null;
    return this.rowToSummary(row);
  }

  saveConversationLog(conversationLog: Omit<ConversationLog, 'generatedAt'>): void {
    const stmt = this.db.prepare(`
      INSERT OR REPLACE INTO conversation_logs
      (id, meeting_id, topics, model_id, generated_at)
      VALUES (?, ?, ?, ?, ?)
    `);
    stmt.run(
      conversationLog.id,
      conversationLog.meetingId,
      JSON.stringify(conversationLog.topics),
      conversationLog.modelId,
      new Date().toISOString()
    );
  }

  getConversationLogByMeeting(meetingId: string): ConversationLog | null {
    const stmt = this.db.prepare('SELECT * FROM conversation_logs WHERE meeting_id = ?');
    const row = stmt.get(meetingId) as ConversationLogRow | undefined;
    if (!row) return null;
    return this.rowToConversationLog(row);
  }

  close(): void {
    this.db.close();
  }

  private rowToMeeting(row: MeetingRow): Meeting {
    return {
      id: row.id,
      type: row.type as MeetingType,
      title: row.title,
      status: row.status as MeetingStatus,
      language: row.language as TranscribeLanguage,
      startedAt: new Date(row.started_at),
      endedAt: row.ended_at ? new Date(row.ended_at) : undefined,
      duration: row.duration,
      metadata: this.parseMetadata(row.metadata),
      createdAt: new Date(row.created_at),
      updatedAt: new Date(row.updated_at),
    };
  }

  /**
   * 메타데이터 JSON 문자열을 파싱하고 검증합니다.
   * ORCH-005, ORCH-017: 파싱 실패 시 로깅 및 기본값 반환
   */
  private parseMetadata(metadataStr: string | null | undefined): MeetingMetadata {
    if (!metadataStr) return {};
    try {
      const parsed = JSON.parse(metadataStr);
      const result = MeetingMetadataSchema.safeParse(parsed);
      if (!result.success) {
        log.warn({ error: result.error.message }, 'Metadata validation failed');
        return {};
      }
      return result.data as MeetingMetadata;
    } catch (error) {
      log.error({ err: error }, 'Failed to parse metadata JSON');
      return {};
    }
  }

  private rowToSegment(row: TranscriptionSegmentRow): TranscriptionSegment {
    return {
      id: row.id,
      meetingId: row.meeting_id,
      resultId: row.result_id,
      text: row.text,
      startTime: row.start_time,
      endTime: row.end_time,
      speakerLabel: row.speaker_label,
      confidence: row.confidence || undefined,
      createdAt: new Date(row.created_at),
    };
  }

  /**
   * 교정된 문장 DB row를 객체로 변환합니다.
   * ORCH-017: segment_ids JSON 스키마 검증 추가
   */
  private rowToCorrectedSentence(row: CorrectedSentenceRow): CorrectedSentence {
    let segmentIds: string[] = [];
    if (row.segment_ids) {
      try {
        const parsed = JSON.parse(row.segment_ids);
        const result = SegmentIdsSchema.safeParse(parsed);
        if (result.success) {
          segmentIds = result.data;
        } else {
          log.warn({ error: result.error.message }, 'segment_ids validation failed');
        }
      } catch (error) {
        log.error({ err: error }, 'Failed to parse segment_ids JSON');
        segmentIds = [];
      }
    }
    return {
      id: row.id,
      meetingId: row.meeting_id,
      originalText: row.original_text,
      correctedText: row.corrected_text,
      translatedText: row.translated_text ?? null,
      segmentIds,
      startTime: row.start_time,
      endTime: row.end_time,
      speakerLabel: row.speaker_label,
      modelId: row.model_id,
      correctedAt: new Date(row.corrected_at),
    };
  }

  /**
   * 요약 DB row를 객체로 변환합니다.
   * ORCH-017: 모든 JSON 필드에 스키마 검증 추가
   */
  private rowToSummary(row: MeetingSummaryRow): MeetingSummary {
    // 각 JSON 필드를 안전하게 파싱하고 검증
    const parseAndValidate = <T>(
      jsonStr: string,
      schema: z.ZodType<T>,
      fieldName: string
    ): T => {
      try {
        const parsed = JSON.parse(jsonStr);
        const result = schema.safeParse(parsed);
        if (result.success) {
          return result.data;
        }
        log.warn({ field: fieldName, error: result.error.message }, 'Summary field validation failed');
        return schema.parse(undefined); // Return default from .catch()
      } catch (error) {
        log.error({ field: fieldName, err: error }, 'Failed to parse summary field JSON');
        return schema.parse(undefined); // Return default from .catch()
      }
    };

    return {
      id: row.id,
      meetingId: row.meeting_id,
      mainTopics: parseAndValidate(row.main_topics, MainTopicsSchema, 'mainTopics'),
      topicDiscussions: parseAndValidate(row.topic_discussions, TopicDiscussionsSchema, 'topicDiscussions'),
      keyTakeaways: parseAndValidate(row.key_takeaways, KeyTakeawaysSchema, 'keyTakeaways'),
      confirmedActions: parseAndValidate(row.confirmed_actions, ActionsSchema, 'confirmedActions'),
      pendingActions: parseAndValidate(row.pending_actions, ActionsSchema, 'pendingActions'),
      followUps: parseAndValidate(row.follow_ups, FollowUpsSchema, 'followUps'),
      openIssues: parseAndValidate(row.open_issues, OpenIssuesSchema, 'openIssues'),
      generatedAt: new Date(row.generated_at),
      modelId: row.model_id,
    };
  }

  /**
   * 대화 로그 DB row를 객체로 변환합니다.
   */
  private rowToConversationLog(row: ConversationLogRow): ConversationLog {
    let topics: ConversationTopic[] = [];
    try {
      const parsed = JSON.parse(row.topics);
      const result = ConversationTopicsSchema.safeParse(parsed);
      if (result.success) {
        topics = result.data;
      } else {
        log.warn({ error: result.error.message }, 'ConversationLog topics validation failed');
      }
    } catch (error) {
      log.error({ err: error }, 'Failed to parse conversation log topics JSON');
    }

    return {
      id: row.id,
      meetingId: row.meeting_id,
      topics,
      generatedAt: new Date(row.generated_at),
      modelId: row.model_id,
    };
  }
}
