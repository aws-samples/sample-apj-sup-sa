export interface SchemaVersionRow {
  version: number;
  applied_at: string;
}

export interface MeetingRow {
  id: string;
  type: string;
  title: string;
  status: string;
  language: string;
  started_at: string;
  ended_at: string | null;
  duration: number;
  metadata: string;
  created_at: string;
  updated_at: string;
}

export interface TranscriptionSegmentRow {
  id: string;
  meeting_id: string;
  result_id: string;
  text: string;
  start_time: number;
  end_time: number;
  speaker_label: string | null;
  confidence: number | null;
  created_at: string;
}

export interface CorrectedSentenceRow {
  id: string;
  meeting_id: string;
  original_text: string;
  corrected_text: string;
  translated_text: string | null;
  segment_ids: string | null;
  start_time: number;
  end_time: number;
  speaker_label: string | null;
  model_id: string;
  corrected_at: string;
}

export interface MeetingSummaryRow {
  id: string;
  meeting_id: string;
  main_topics: string;
  topic_discussions: string;
  key_takeaways: string;
  confirmed_actions: string;
  pending_actions: string;
  follow_ups: string;
  open_issues: string;
  generated_at: string;
  model_id: string;
}

export interface ConversationLogRow {
  id: string;
  meeting_id: string;
  topics: string;  // JSON 배열
  generated_at: string;
  model_id: string;
}
