// Interview duration picker (item 3) — lets the student choose how long the interview runs. The
// chosen minutes drive the number of questions the backend plans (~90s each), and the coach wraps up
// when the plan is consumed. Purely a length control; the difficulty picker still shapes behavior.

import type { DurationMinutes } from "../lib/sessionApi";

interface Props {
  value: DurationMinutes;
  onChange: (d: DurationMinutes) => void;
  disabled?: boolean;
}

// Label each option with the rough question count so the choice is transparent (mirrors the backend
// ~90s/question mapping: 3->2, 5->3, 10->6, 15->9, 30->16). 3 min is the quick test drive
// (F008 US5): a complete miniature interview for first-timers, demos, and smoke checks.
const OPTIONS: { value: DurationMinutes; label: string; sub: string }[] = [
  { value: 3, label: "3 min", sub: "quick test drive" },
  { value: 5, label: "5 min", sub: "~3 questions" },
  { value: 10, label: "10 min", sub: "~6 questions" },
  { value: 15, label: "15 min", sub: "~9 questions" },
  { value: 30, label: "30 min", sub: "~16 questions" },
];

export default function DurationPicker({ value, onChange, disabled }: Props) {
  return (
    <div className="durs" style={{ opacity: disabled ? 0.6 : 1, pointerEvents: disabled ? "none" : "auto" }}>
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          className={"dur" + (value === o.value ? " sel" : "")}
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
        >
          <b>{o.label}</b>
          <span>{o.sub}</span>
        </button>
      ))}
    </div>
  );
}
