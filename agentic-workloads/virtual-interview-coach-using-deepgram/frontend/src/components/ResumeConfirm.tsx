// Parse-back confirmation (T021) — shows the parsed resume facts for review/correct, with
// a manual-entry fallback when parsing is low-confidence or fails (FR-201 / SC-006).
// The CONFIRMED facts (not the raw parse) become authoritative for grounding (FR-204).

import { useState } from "react";
import type {
  EducationItem,
  ExperienceItem,
  ParseStatus,
  ParsedFacts,
} from "../lib/setupApi";

interface Props {
  initialFacts: ParsedFacts;
  // "parsed" -> review/correct framing; "low_confidence"/"failed" -> manual-entry framing (SC-006).
  parseStatus: ParseStatus;
  busy?: boolean;
  onConfirm: (facts: ParsedFacts, manualEntry: boolean) => void;
}

// Editable review of the parsed facts. The student can correct any field before confirming; when the
// parse failed the same form doubles as the manual-entry path (it starts empty and `manual_entry` is
// reported true). Confirmation is what makes the facts authoritative for grounding.
export default function ResumeConfirm({ initialFacts, parseStatus, busy, onConfirm }: Props) {
  const manualMode = parseStatus !== "parsed";
  const [facts, setFacts] = useState<ParsedFacts>(() => normalize(initialFacts));
  // Skills are edited as raw comma-separated text and only split into an array at submit. Deriving
  // the input value from a filtered array on every keystroke would erase the comma you just typed.
  const [skillsText, setSkillsText] = useState<string>(() => (initialFacts.skills ?? []).join(", "));

  const set = (patch: Partial<ParsedFacts>) => setFacts((f) => ({ ...f, ...patch }));

  function updateExperience(i: number, patch: Partial<ExperienceItem>) {
    setFacts((f) => {
      const experience = f.experience.slice();
      experience[i] = { ...experience[i], ...patch };
      return { ...f, experience };
    });
  }
  function addExperience() {
    set({ experience: [...facts.experience, { title: "", organization: "", duration: "", highlights: [] }] });
  }
  function removeExperience(i: number) {
    set({ experience: facts.experience.filter((_, idx) => idx !== i) });
  }

  function updateEducation(i: number, patch: Partial<EducationItem>) {
    setFacts((f) => {
      const education = f.education.slice();
      education[i] = { ...education[i], ...patch };
      return { ...f, education };
    });
  }
  function addEducation() {
    set({ education: [...facts.education, { qualification: "", institution: "", year: "" }] });
  }
  function removeEducation(i: number) {
    set({ education: facts.education.filter((_, idx) => idx !== i) });
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    // Skills live in raw text until now; split into the array shape only at submit.
    onConfirm(cleanup({ ...facts, skills: splitList(skillsText) }), manualMode);
  }

  return (
    <form onSubmit={submit} className="card">
      {manualMode ? (
        <p role="alert" style={notice}>
          {parseStatus === "failed"
            ? "We couldn't read that file. Please enter your details below — this is all we use to tailor your interview."
            : "We weren't fully confident reading your resume. Please review and correct the details below."}
        </p>
      ) : (
        <p className="hint" style={{ fontSize: 13 }}>
          Here's what we pulled from your resume. Review and fix anything that's off, then confirm —
          these details are what we use to tailor your interview.
        </p>
      )}

      <label style={label}>
        Name
        <input
          style={input}
          value={facts.name ?? ""}
          onChange={(e) => set({ name: e.target.value })}
        />
      </label>

      <label style={label}>
        Summary
        <textarea
          style={{ ...input, minHeight: 64 }}
          value={facts.summary ?? ""}
          onChange={(e) => set({ summary: e.target.value })}
          placeholder="A sentence or two about your background."
        />
      </label>

      <label style={label}>
        Skills (comma-separated)
        <input
          style={input}
          value={skillsText}
          onChange={(e) => setSkillsText(e.target.value)}
          placeholder="e.g. Python, SQL, project management"
        />
      </label>

      <fieldset style={group}>
        <legend style={legend}>Experience</legend>
        {facts.experience.map((exp, i) => (
          <div key={i} style={card}>
            <input
              style={input}
              value={exp.title}
              onChange={(e) => updateExperience(i, { title: e.target.value })}
              placeholder="Job title"
            />
            <input
              style={input}
              value={exp.organization ?? ""}
              onChange={(e) => updateExperience(i, { organization: e.target.value })}
              placeholder="Organization"
            />
            <input
              style={input}
              value={exp.duration ?? ""}
              onChange={(e) => updateExperience(i, { duration: e.target.value })}
              placeholder="Duration (e.g. 2021-2024)"
            />
            <textarea
              style={{ ...input, minHeight: 48 }}
              value={exp.highlights.join("\n")}
              onChange={(e) => updateExperience(i, { highlights: e.target.value.split("\n") })}
              placeholder="Key achievements (one per line)"
            />
            <button type="button" style={linkButton} onClick={() => removeExperience(i)}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" style={addButton} onClick={addExperience}>
          + Add experience
        </button>
      </fieldset>

      <fieldset style={group}>
        <legend style={legend}>Education</legend>
        {facts.education.map((ed, i) => (
          <div key={i} style={card}>
            <input
              style={input}
              value={ed.qualification}
              onChange={(e) => updateEducation(i, { qualification: e.target.value })}
              placeholder="Qualification (e.g. BSc Computer Science)"
            />
            <input
              style={input}
              value={ed.institution ?? ""}
              onChange={(e) => updateEducation(i, { institution: e.target.value })}
              placeholder="Institution"
            />
            <input
              style={input}
              value={ed.year ?? ""}
              onChange={(e) => updateEducation(i, { year: e.target.value })}
              placeholder="Year"
            />
            <button type="button" style={linkButton} onClick={() => removeEducation(i)}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" style={addButton} onClick={addEducation}>
          + Add education
        </button>
      </fieldset>

      <button type="submit" disabled={busy} style={confirmButton}>
        {busy ? "Saving…" : "Confirm these details"}
      </button>
    </form>
  );
}

// --- helpers --------------------------------------------------------------------------------

function normalize(facts: ParsedFacts): ParsedFacts {
  return {
    name: facts.name ?? "",
    summary: facts.summary ?? "",
    skills: facts.skills ?? [],
    experience: (facts.experience ?? []).map((e) => ({
      title: e.title ?? "",
      organization: e.organization ?? "",
      duration: e.duration ?? "",
      highlights: e.highlights ?? [],
    })),
    education: (facts.education ?? []).map((e) => ({
      qualification: e.qualification ?? "",
      institution: e.institution ?? "",
      year: e.year ?? "",
    })),
  };
}

// Drop empty rows so confirmed facts stay clean (a blank experience card adds nothing to grounding).
function cleanup(facts: ParsedFacts): ParsedFacts {
  return {
    name: (facts.name ?? "").trim() || null,
    summary: (facts.summary ?? "").trim() || null,
    skills: facts.skills.map((s) => s.trim()).filter(Boolean),
    experience: facts.experience
      .filter((e) => (e.title ?? "").trim() || (e.organization ?? "").trim())
      .map((e) => ({
        title: (e.title ?? "").trim(),
        organization: (e.organization ?? "").trim() || null,
        duration: (e.duration ?? "").trim() || null,
        highlights: e.highlights.map((h) => h.trim()).filter(Boolean),
      })),
    education: facts.education
      .filter((e) => (e.qualification ?? "").trim() || (e.institution ?? "").trim())
      .map((e) => ({
        qualification: (e.qualification ?? "").trim(),
        institution: (e.institution ?? "").trim() || null,
        year: (e.year ?? "").trim() || null,
      })),
  };
}

const splitList = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);

// --- styles (F007 — aligned to the design tokens in theme.css) ------------------------------

const label: React.CSSProperties = {
  display: "block", margin: "0.75rem 0", fontSize: 13, fontWeight: 600, color: "var(--ink)",
};
const input: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "12px 13px",
  marginTop: "0.4rem",
  fontSize: 14,
  fontFamily: "var(--font)",
  border: "1px solid var(--line)",
  borderRadius: 10,
  background: "var(--surface-2)",
  color: "var(--ink)",
  boxSizing: "border-box",
};
const group: React.CSSProperties = {
  border: "1px solid var(--line)",
  borderRadius: "var(--radius)",
  padding: "0.75rem 1rem",
  margin: "1rem 0",
};
const legend: React.CSSProperties = { fontWeight: 600, fontSize: 13, color: "var(--ink)" };
const card: React.CSSProperties = {
  border: "1px solid var(--line)",
  borderRadius: 10,
  padding: "0.75rem",
  marginBottom: "0.75rem",
  display: "grid",
  gap: "0.5rem",
  background: "var(--surface-2)",
};
const notice: React.CSSProperties = {
  background: "var(--accent-soft)",
  border: "1px solid #ffcc80",
  borderRadius: "var(--radius-sm)",
  padding: "0.7rem 0.9rem",
  fontSize: 13,
  color: "#9a5a1c",
};
const linkButton: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "var(--danger)",
  cursor: "pointer",
  padding: 0,
  justifySelf: "start",
  fontSize: "0.85rem",
};
const addButton: React.CSSProperties = {
  background: "none",
  border: "1px dashed var(--ink-faint)",
  borderRadius: "var(--radius-sm)",
  padding: "0.5rem 0.9rem",
  cursor: "pointer",
  color: "var(--ink-soft)",
  fontFamily: "var(--font)",
};
const confirmButton: React.CSSProperties = {
  padding: "13px 22px",
  fontSize: 15,
  fontWeight: 600,
  cursor: "pointer",
  marginTop: "0.75rem",
  border: "none",
  borderRadius: 12,
  background: "var(--primary)",
  color: "#fff",
  fontFamily: "var(--font)",
  boxShadow: "0 6px 16px rgba(47,143,157,.3)",
};
