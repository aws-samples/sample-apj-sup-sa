// Difficulty picker (T030) — Easy / Moderate / Difficult selector wired into Setup.tsx (FR-213).
// The chosen tier is recorded on the session (never blended into a composite — SC-008) and its
// difficulty_profile levers shape the live persona so the tiers are behaviorally distinct (SC-004).

import type { Difficulty } from "../lib/sessionApi";

interface Props {
  value: Difficulty;
  onChange: (d: Difficulty) => void;
  disabled?: boolean;
}

// Plain-language descriptions of how each tier feels, so the choice is transparent to the student
// (FR-213). The behavioral levers themselves live server-side in difficulty_profile.
const TIERS: { value: Difficulty; label: string; blurb: string }[] = [
  { value: "easy", label: "Easy", blurb: "Warm and encouraging, with gentle follow-ups and hints when you're stuck." },
  { value: "moderate", label: "Moderate", blurb: "A realistic interview: steady probing for specifics, few hints." },
  { value: "difficult", label: "Difficult", blurb: "Demanding and probing, with curveballs and no hints — like a tough panel." },
];

const EMOJI: Record<Difficulty, string> = { easy: "🌱", moderate: "⚖️", difficult: "🔥" };

export default function DifficultyPicker({ value, onChange, disabled }: Props) {
  return (
    <div className="levels" style={{ opacity: disabled ? 0.6 : 1, pointerEvents: disabled ? "none" : "auto" }}>
      {TIERS.map((t) => (
        <button
          type="button"
          key={t.value}
          className={"lvl" + (value === t.value ? " sel" : "")}
          onClick={() => onChange(t.value)}
          aria-pressed={value === t.value}
        >
          <div className="lh"><b>{t.label}</b><span className="emoji">{EMOJI[t.value]}</span></div>
          {t.value === "easy" && <span className="rec">GENTLE START</span>}
          <p>{t.blurb}</p>
        </button>
      ))}
    </div>
  );
}
