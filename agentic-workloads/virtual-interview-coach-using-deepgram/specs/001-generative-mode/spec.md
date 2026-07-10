# Feature Specification: Generative Mode — Bank-Optional Interview Generation

**Feature Branch**: `add-virtual-interview-coach-deepgram` (spec dir `001-generative-mode`)

**Created**: 2026-06-25

**Status**: Draft

**Input**: User description: "Run a complete, personalized interview with no seeded question
bank by generating the full question plan (general/behavioral AND domain) in the prep window from
the confirmed resume + job description + difficulty + duration. Bank stays the default when present;
generative mode fills gaps or is enabled deliberately. Sanctioned by Constitution Principle VII."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start an interview on a freshly deployed instance with no seeded bank (Priority: P1)

A student signs in to a newly deployed instance whose question bank has never been seeded. They
complete the setup wizard (consent → resume → confirm → role → difficulty & length) and press
"Start interview" at any difficulty and any duration. Instead of failing, the system generates a
complete, resume- and JD-grounded set of questions during the brief preparation step, and the
session begins normally.

**Why this priority**: This is the MVP. Today this exact path fails with a 503
("no approved archetypes available for difficulty='easy'"), making a fresh deployment unusable
without a manual operator bank-load. Removing that hard dependency is the whole point of the feature.

**Independent Test**: With an empty question store, complete the wizard and start an Easy / 3-minute
interview; the session reaches the mic-check / live stage with `blueprint_ready = true` and no error
banner.

**Acceptance Scenarios**:

1. **Given** an empty question store and a confirmed resume + pasted JD, **When** the student starts
   an Easy 3-minute interview, **Then** preparation succeeds, a full question plan exists for the
   session, and no "no approved archetypes" error is shown.
2. **Given** the same empty store, **When** the student starts a Difficult 30-minute interview,
   **Then** preparation produces enough questions to fill that longer interview at the harder tier.
3. **Given** a session that ran on generated questions, **When** its report is later produced,
   **Then** the report does not claim the questions were human-vetted, and the session is flagged as
   coverage-reduced / generative.

---

### User Story 2 - Role not covered by the bank still gets role-specific questions (Priority: P2)

A student targeting a role the vetted bank does not cover (e.g. a niche or novel job title) still
receives role-specific (domain) questions, generated on the fly, composed alongside the
behavioral/general questions — so the interview feels tailored, not generic.

**Why this priority**: Partial support already exists (domain-only just-in-time generation), but it
relied on general questions coming from the bank. With the bank empty or thin, the general questions
must also be generated so the composed interview still has a natural behavioral→technical arc.

**Independent Test**: With a JD for an uncovered role and a thin/empty bank, start an interview and
confirm the plan contains both behavioral questions and role-specific questions grounded in the JD.

**Acceptance Scenarios**:

1. **Given** a JD whose role the bank does not cover and an empty general pool, **When** the student
   starts an interview, **Then** the plan contains both generated behavioral and generated
   role-specific questions, ordered as a coherent interview.

---

### User Story 3 - Operator can force and observe generative mode (Priority: P3)

An operator can turn generative mode on even when a bank exists (for testing or a demo), via an
environment setting, and can tell from logs and per-session flags which sessions ran on generated
questions versus the vetted bank.

**Why this priority**: Useful for testing and demos and for auditing the Principle VII relaxation,
but not required for the core "fresh deploy works" outcome.

**Independent Test**: Set the operator flag on an instance that has an approved bank, start a
session, and confirm from logs/flags that the session used generated questions.

**Acceptance Scenarios**:

1. **Given** an approved bank is present and the operator flag is OFF, **When** a student starts an
   interview, **Then** behavior is unchanged (bank-served questions) — this is the default.
2. **Given** the operator flag is ON, **When** a student starts an interview, **Then** the plan is
   generated even though the bank could have served it, and this is recorded in the logs and the
   session flag.

---

### Edge Cases

- **Generation fails or returns nothing** (model error, empty output): preparation MUST degrade
  honestly — it MUST NOT crash in a new way. If neither the bank nor generation can produce any
  questions, the student sees a clear, honest "couldn't prepare your interview, please try again"
  outcome rather than an opaque 503 stack message.
- **Partial generation** (some questions generated, some fail to embed): the interview still starts
  with the questions that succeeded, as long as the minimum needed to fill the chosen duration is met.
- **Very short (3 min) vs very long (30 min) durations**: the number of generated questions scales
  to the chosen duration, same as bank-served plans.
- **Bank partially populated** (some difficulties seeded, others empty): a difficulty with no
  approved rows is served generatively while seeded difficulties continue to use the bank.
- **Re-preparing the same session/role**: regenerating for the same role + difficulty MUST NOT
  create unbounded duplicate stored questions (idempotent persistence).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST be able to prepare and start a complete interview when the question
  store contains no human-approved questions at the chosen difficulty.
- **FR-002**: When generating, the system MUST produce BOTH behavioral/general questions and
  role-specific/domain questions, grounded in the student's confirmed resume facts and the pasted
  job description.
- **FR-003**: Generated questions MUST be calibrated to the chosen difficulty tier (Easy / Moderate
  / Difficult remain behaviorally distinct).
- **FR-004**: The number of questions prepared MUST scale to the chosen interview duration, matching
  the sizing used for bank-served interviews.
- **FR-005**: All large-model generation MUST occur during the preparation step (before the live
  conversation begins) and MUST NOT occur during the live turn-taking. The live question-selection
  step MUST remain a pure data lookup (no model call).
- **FR-006**: Generated questions MUST be stored marked as generated (distinguishable from
  human-vetted questions), and the use of the human-review relaxation MUST be logged per session.
- **FR-007**: A session that ran on generated questions MUST be flagged so downstream reporting never
  implies the questions were human-vetted.
- **FR-008**: When an approved bank IS present at the chosen difficulty, default behavior MUST be
  unchanged (bank-served), UNLESS an operator setting explicitly forces generative mode.
- **FR-009**: The system MUST provide an operator setting to force generative mode on regardless of
  bank contents, for testing/demo and audit.
- **FR-010**: If generation fails, preparation MUST fail gracefully and honestly (clear user-facing
  message), and MUST NOT regress the existing behavior for instances that DO have a seeded bank.
- **FR-011**: Persisting generated questions MUST be idempotent for the same role + difficulty so
  re-preparation does not accumulate duplicates without bound.
- **FR-012**: The feature MUST NOT require a destructive database change; any new stored field MUST
  be additive.

### Key Entities *(include if feature involves data)*

- **Question (archetype)**: a single interview question with its difficulty, category
  (general/behavioral vs domain/role-specific), follow-up probes, provenance (human-vetted vs
  generated), and the embedding used for live selection.
- **Interview plan (blueprint)**: the ordered set of questions chosen for one session, the
  competencies it targets, and whether its coverage was reduced / generated.
- **Session**: one student interview attempt; carries the chosen difficulty and duration, and the
  flag indicating whether it ran on generated questions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On an instance with an empty question store, a student can start an interview at every
  difficulty (Easy/Moderate/Difficult) and every offered duration, with a 0% rate of
  "no approved questions" failures.
- **SC-002**: The live turn-taking latency is unchanged by this feature — the existing live-latency
  gate (sub-second response gap) still passes, because no generation happens on the live path.
- **SC-003**: 100% of sessions that ran on generated questions are identifiable as such from their
  stored flag and from logs (no silent un-vetted serving).
- **SC-004**: On an instance with a seeded approved bank and the operator flag OFF, interview
  preparation behavior is identical to today (no regression).
- **SC-005**: A complete interview can be prepared from an empty store within the same preparation
  window budget already used for personalized sessions (no user-perceived stall beyond the existing
  "preparing" step).

## Assumptions

- A confirmed resume and a pasted job description are present before an interview starts (existing
  precondition; generative mode does not relax it).
- The existing preparation window (after the student presses start, before the live conversation) is
  the correct place for generation; users already experience a brief "preparing" step there.
- Generated questions are acceptable to serve without human review for instances/roles the bank does
  not cover — this is the explicit, bounded relaxation sanctioned by Constitution Principle VII, not
  a new policy decision made here.
- An operator-level environment setting (not an end-user UI toggle) is sufficient to force generative
  mode; a UI control is out of scope.
- Reusing the existing question store and its provenance markers is preferred over introducing new
  storage; a new flag, if needed, is additive.

## Out of Scope

- Changing the live turn loop, the scoring rubric, or the human bank-review pipeline.
- Building an end-user UI toggle for generative mode.
- Improving or re-ranking the bank itself; this feature only adds the generative fallback/override.
