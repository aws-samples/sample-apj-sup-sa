// F008 US4: the coaching dashboard renders prose guidance, the empty state, and session links.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Dashboard from "./Dashboard";

const GUIDANCE = {
  available: true,
  generated_at: "2026-06-12T04:30:00Z",
  sessions_analyzed: 3,
  strengths: ["You anchor answers in concrete projects."],
  improvement_areas: ["Results stay unquantified."],
  trend_note: "Structure scores climbed across your last three sessions.",
  next_actions: ["Prepare two metrics.", "Close each answer with an outcome."],
};

const SESSIONS = [
  {
    session_id: "s1", created_at: "2026-06-11T20:00:00Z", ended_at: null,
    end_reason: "completed", job_title: "Cloud Engineer", difficulty: "moderate",
    duration_minutes: 5, report_status: "scored",
  },
];

function mockFetch(guidance: unknown, sessions: unknown = SESSIONS) {
  return vi.fn((url: RequestInfo | URL) => {
    const u = String(url);
    if (u === "/api/me/guidance") {
      return Promise.resolve(new Response(JSON.stringify(guidance), { status: 200 }));
    }
    if (u === "/api/sessions") {
      return Promise.resolve(new Response(JSON.stringify({ sessions }), { status: 200 }));
    }
    return Promise.resolve(new Response("{}", { status: 404 }));
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Dashboard (US4)", () => {
  it("renders the coach prose with last-updated and session count", async () => {
    vi.stubGlobal("fetch", mockFetch(GUIDANCE));
    render(<Dashboard accessToken="t" onPracticeAgain={() => {}} />);
    expect(await screen.findByText(/coach's notes/i)).toBeInTheDocument();
    expect(screen.getByText(/based on 3 sessions/i)).toBeInTheDocument();
    expect(screen.getByText(/anchor answers in concrete projects/i)).toBeInTheDocument();
    expect(screen.getByText(/Results stay unquantified/i)).toBeInTheDocument();
    expect(screen.getByText(/Structure scores climbed/i)).toBeInTheDocument();
    expect(screen.getByText(/Prepare two metrics/i)).toBeInTheDocument();
    // session history with a report link
    expect(screen.getByText("Cloud Engineer")).toBeInTheDocument();
  });

  it("shows the empty/encouragement state when no guidance exists", async () => {
    vi.stubGlobal("fetch", mockFetch({ available: false }, []));
    render(<Dashboard accessToken="t" onPracticeAgain={() => {}} />);
    expect(await screen.findByText(/coach's notes appear here/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start your first interview/i })).toBeInTheDocument();
  });

  it("explains pending analysis when sessions exist but guidance doesn't yet", async () => {
    vi.stubGlobal("fetch", mockFetch({ available: false }, SESSIONS));
    render(<Dashboard accessToken="t" onPracticeAgain={() => {}} />);
    expect(await screen.findByText(/being analyzed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /practice again/i })).toBeInTheDocument();
  });
});
