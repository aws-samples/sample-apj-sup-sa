// F008 US1 (report area: latest-by-default + static session label, hidden while summarizing) +
// US2 (transcript grouping/visibility). Session SELECTION lives on the Dashboard (Dashboard.test).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportArea, groupTurns } from "./Report";

const SESSIONS = [
  {
    session_id: "s-new", created_at: "2026-06-12T10:00:00Z", ended_at: null,
    end_reason: "completed", job_title: "Cloud Engineer", difficulty: "moderate",
    duration_minutes: 5, report_status: "scored",
  },
  {
    session_id: "s-old", created_at: "2026-06-10T09:00:00Z", ended_at: null,
    end_reason: "completed", job_title: null, difficulty: null,
    duration_minutes: 3, report_status: "failed",
  },
];

const SCORED = {
  status: "scored",
  report: {
    id: "r1", status: "scored", overall: "7.0", score_content: "7.0",
    score_structure: "7.0", score_communication: "7.0", score_confidence: "7.0",
    difficulty: "moderate", rubric_version: "v2", summary_strengths: [],
    summary_improvements: [], metrics: {}, competency_scorecard: [], question_feedback: [],
  },
};

function mockFetch(opts: { sessions?: unknown; reportStatus?: "scored" | "queued" } = {}) {
  return vi.fn((url: RequestInfo | URL) => {
    const u = String(url);
    if (u === "/api/sessions") {
      return Promise.resolve(new Response(JSON.stringify({ sessions: opts.sessions ?? SESSIONS }), { status: 200 }));
    }
    if (u.includes("/report")) {
      const body = opts.reportStatus === "queued" ? { status: "queued", report: null } : SCORED;
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    }
    // session detail (turns for audio map + transcript)
    return Promise.resolve(new Response(JSON.stringify({
      turns: [
        { turn_id: "t0", turn_index: 0, speaker: "coach", transcript: "Tell me about yourself.", has_audio: false },
        { turn_id: "t1", turn_index: 1, speaker: "student", transcript: "I am Jordan,", has_audio: true },
        { turn_id: "t2", turn_index: 2, speaker: "student", transcript: "a cloud engineer.", has_audio: true },
      ],
    }), { status: 200 }));
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReportArea (US1: latest by default, static label)", () => {
  it("shows the LATEST session's report with its identity label", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ReportArea accessToken="t" />);
    // the scored report renders for s-new (newest first), with the static label
    expect(await screen.findByText(/Practice session/i)).toBeInTheDocument();
    expect(screen.getByText(/Cloud Engineer \(moderate\)/)).toBeInTheDocument();
    // it is a LABEL, not a dropdown
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("hides the label on the summarizing/processing screen", async () => {
    vi.stubGlobal("fetch", mockFetch({ reportStatus: "queued" }));
    render(<ReportArea accessToken="t" sessionId="s-new" />);
    expect(await screen.findByText(/Analyzing your interview/i)).toBeInTheDocument();
    expect(screen.queryByText(/Practice session/i)).not.toBeInTheDocument();
  });

  it("shows an explicitly selected session (Dashboard pick / just-ended)", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ReportArea accessToken="t" sessionId="s-old" />);
    // s-old's label, not the latest one
    await waitFor(() => {
      expect(screen.getByText(/Practice interview/)).toBeInTheDocument();
    });
  });

  it("shows the empty state for zero sessions", async () => {
    vi.stubGlobal("fetch", mockFetch({ sessions: [] }));
    render(<ReportArea accessToken="t" />);
    expect(await screen.findByText(/No practice sessions yet/i)).toBeInTheDocument();
  });
});

describe("transcript (US2)", () => {
  it("groups consecutive same-speaker fragments", () => {
    const groups = groupTurns([
      { turn_id: "a", turn_index: 0, speaker: "coach", transcript: "Hi." },
      { turn_id: "b", turn_index: 1, speaker: "student", transcript: "I am Jordan," },
      { turn_id: "c", turn_index: 2, speaker: "student", transcript: "a cloud engineer." },
      { turn_id: "d", turn_index: 3, speaker: "coach", transcript: "Great." },
    ]);
    expect(groups).toEqual([
      { speaker: "coach", text: "Hi." },
      { speaker: "student", text: "I am Jordan, a cloud engineer." },
      { speaker: "coach", text: "Great." },
    ]);
  });

  it("skips blank fragments and renders in processing state", async () => {
    expect(groupTurns([{ turn_id: "a", turn_index: 0, speaker: "coach", transcript: "  " }])).toEqual([]);
    vi.stubGlobal("fetch", mockFetch({ reportStatus: "queued" }));
    render(<ReportArea accessToken="t" sessionId="s-new" />);
    expect(await screen.findByText(/Analyzing your interview/i)).toBeInTheDocument();
    expect(await screen.findByText(/Full transcript/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Full transcript/i));
    expect(screen.getByText(/a cloud engineer/i)).toBeInTheDocument();
  });
});
