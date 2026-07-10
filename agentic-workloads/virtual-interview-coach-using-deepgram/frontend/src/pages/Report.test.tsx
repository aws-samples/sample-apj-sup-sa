// Renders the scored Report with every score delivered as a NUMERIC string (exactly what the API
// can produce) and asserts the screen shows real values — the regression that previously blanked
// the SPA. Also pins the error/processing branches and the error boundary fallback.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Report from "./Report";
import ErrorBoundary from "../components/ErrorBoundary";

// A scored envelope whose scores are ALL strings (Postgres NUMERIC over JSON).
const STRING_SCORED = {
  status: "scored",
  report: {
    id: "r1",
    status: "scored",
    overall: "7.5",
    score_content: "8.0",
    score_structure: "6.5",
    score_communication: "7.0",
    score_confidence: "6.0",
    difficulty: "moderate",
    rubric_version: "v2",
    summary_strengths: ["Clear structure"],
    summary_improvements: ["Quantify results"],
    metrics: { filler_count: 3, wpm: 140 },
    competency_scorecard: [
      {
        competency: "Leadership",
        score_1_5: "4.0", // string on purpose — CompCard previously bypassed num()
        evidence_quote: "I led the migration",
        star_element: "action",
        turn_index: 1,
        assessed: true,
      },
    ],
    question_feedback: [],
  },
};

function mockFetch(envelope: unknown) {
  return vi.fn((url: RequestInfo | URL) => {
    const u = String(url);
    if (u.includes("/report")) {
      return Promise.resolve(new Response(JSON.stringify(envelope), { status: 200 }));
    }
    // The turns lookup (per-answer playback map) — empty is fine for these tests.
    return Promise.resolve(new Response(JSON.stringify({ turns: [] }), { status: 200 }));
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Report with NUMERIC-string scores", () => {
  it("renders all scores instead of blanking", async () => {
    vi.stubGlobal("fetch", mockFetch(STRING_SCORED));
    render(<Report accessToken="t" sessionId="s1" />);

    // Overall score circle formats the string "7.5".
    expect(await screen.findByText("7.5")).toBeInTheDocument();
    // Sub-scores coerced and formatted.
    expect(screen.getByText("8.0")).toBeInTheDocument();
    expect(screen.getByText("6.5")).toBeInTheDocument();
    // Competency card: string score_1_5 must not render "NaN / 5".
    expect(screen.getByText(/4 \/ 5/)).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("shows the failed branch on a failed report", async () => {
    vi.stubGlobal("fetch", mockFetch({ status: "failed", report: null }));
    render(<Report accessToken="t" sessionId="s1" />);
    expect(await screen.findByText(/couldn't finish scoring/i)).toBeInTheDocument();
  });

  it("shows the processing bridge while queued", async () => {
    vi.stubGlobal("fetch", mockFetch({ status: "queued", report: null }));
    render(<Report accessToken="t" sessionId="s1" />);
    expect(await screen.findByText(/Analyzing your interview/i)).toBeInTheDocument();
  });
});

describe("ErrorBoundary", () => {
  it("catches a render error and shows the recovery screen", async () => {
    const Boom = () => {
      throw new Error("render exploded");
    };
    // Silence React's expected error logging for this intentional throw.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>
    );
    await waitFor(() => {
      expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /reload/i })).toBeInTheDocument();
    spy.mockRestore();
  });
});
