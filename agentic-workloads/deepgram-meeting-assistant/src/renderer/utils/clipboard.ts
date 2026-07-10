import {
  type TranscriptionSegment,
  type CorrectedSentence,
  type MeetingSummary,
  type ConversationLog,
} from '../../shared/types';
import type { QuickMeetingTab } from '../components/meeting-types/types';

export async function copyMeetingContent(
  activeTab: QuickMeetingTab,
  segments: TranscriptionSegment[],
  correctedSentences: CorrectedSentence[],
  fullScript: string,
  summary: MeetingSummary | null | undefined,
  conversationLog?: ConversationLog | null
): Promise<void> {
  let textToCopy = '';

  if (activeTab === 'conversation') {
    // 대화 로그가 있으면 사용, 없으면 빈 문자열
    if (conversationLog && conversationLog.topics.length > 0) {
      const parts = conversationLog.topics.map((topic) => {
        const points = topic.points.map((point) => `- ${point}`).join('\n');
        return `### ${topic.title}\n${points}`;
      });
      textToCopy = parts.join('\n\n');
    }
  } else if (activeTab === 'script') {
    textToCopy = fullScript;
  } else if (activeTab === 'summary' && summary) {
    const parts: string[] = [];

    if (summary.mainTopics.length > 0) {
      parts.push('## 주요 논의 주제\n' + summary.mainTopics.map((t) => `- ${t}`).join('\n'));
    }

    if (summary.topicDiscussions.length > 0) {
      const discParts = summary.topicDiscussions.map((d) => {
        let text = `### ${d.topic}\n`;
        if (d.discussions.length > 0) {
          text += d.discussions.map((item) => `- ${item}`).join('\n');
        }
        if (d.decisions.length > 0) {
          text += '\n**결정:** ' + d.decisions.join(', ');
        }
        return text;
      });
      parts.push('## 논의 및 결정 사항\n' + discParts.join('\n\n'));
    }

    if (summary.keyTakeaways.length > 0) {
      parts.push('## 핵심 요약\n' + summary.keyTakeaways.map((t) => `- ${t}`).join('\n'));
    }

    if (summary.confirmedActions.length > 0) {
      parts.push(
        '## 확정된 액션 아이템\n' +
          summary.confirmedActions
            .map((a) => `- ${a.task} — ${a.owner} — ${a.deadline}`)
            .join('\n')
      );
    }

    if (summary.pendingActions.length > 0) {
      parts.push(
        '## 확인 필요 액션 아이템\n' +
          summary.pendingActions
            .map((a) => `- ${a.task} — ${a.owner} — ${a.deadline}`)
            .join('\n')
      );
    }

    if (summary.followUps.length > 0) {
      parts.push('## 후속 조치\n' + summary.followUps.map((f) => `- ${f}`).join('\n'));
    }

    if (summary.openIssues.length > 0) {
      parts.push('## 미해결 이슈\n' + summary.openIssues.map((i) => `- ${i}`).join('\n'));
    }

    textToCopy = parts.join('\n\n');
  }

  if (textToCopy) {
    try {
      await navigator.clipboard.writeText(textToCopy);
    } catch (err) {
      console.error('Failed to copy', err);
    }
  }
}
