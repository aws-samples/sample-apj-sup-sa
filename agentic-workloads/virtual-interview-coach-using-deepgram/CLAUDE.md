<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

## Active Feature: 009 — Generative Mode (bank-optional interviews; Constitution VII)

Specs in `specs/001-generative-mode/` (spec/plan/research/data-model/contracts/tasks). Adds
Constitution **Principle VII** (`.specify/memory/constitution.md` v1.1.0): a sanctioned, bounded
relaxation of II that lets a bank-LESS deploy generate a COMPLETE personalized interview
(general/behavioral + domain) in the prep window from the confirmed resume + JD + difficulty +
duration, instead of 503-ing with "no approved archetypes". Generalizes the domain-only
`backend/src/prep/jit_generate.py` to also generate general questions
(`generate_general_questions`); `assemble_blueprint` generates when the bank has no general rows at
the difficulty OR `GENERATIVE_MODE=1` is forced. Generated rows are `source='generated'` + a `jit`/
`generative` marker; any generated row flags the session `domain_coverage_reduced` (honest
provenance, Principle V/VII c); generation is prep-window only so live selection stays a pure
pgvector SELECT (Principle I). No schema change (VI). `GENERATIVE_MODE` env + `GenerativeMode` stack
parameter (default 0 = bank-preferred). 5 DB-free decision tests in `backend/tests/test_generative_mode.py`.

- **Build state**: IMPLEMENTED + DEPLOYED (image tag `c2`). Verified live on the deployed stack:
  with `GENERATIVE_MODE=1`, an Easy/3-min `POST /sessions` returned `blueprint_ready=true` +
  `domain_coverage_reduced=true`, and RDS gained 7 generated rows (3 general + 4 domain). The
  deployed default was reset to `GenerativeMode=0` (bank-preferred). The bank loader
  (`bank/load_fixture.py`) remains optional and restores higher-quality vetted questions where run.

## Prior Feature: 008 — Session Review & Coaching Insights (delivers Gate G5)

The plan is `specs/008-session-review-coaching/plan.md`. Read it + its siblings
(`spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, `tasks.md`) before
working on this feature. Five user stories: US1 session-list/report picker (P1, the MVP slice),
US2 full transcript in the report (P2), US3 playback fix (P2 — root cause was presigned URLs
lacking SigV4; S3 400s on SSE-KMS objects; fixed in backend/src/audio_playback.py), US4
coaching dashboard (P3 — coaching_guidance table + report-worker post-scoring refresh in
guidance.py + GET /api/me/guidance; prose over charts; the design-preview Dashboard.tsx was
replaced), US5 3-minute test-drive tier (P3). Constraints: live latency path untouched
(Constitution I), no new composite score (II), owner-scoped 404 everywhere + delete fan-out
covers guidance (III). Schema owner stays voice-worker/src/db_migrate.py (additive).

- **Build state**: IMPLEMENTED + DEPLOYED (tag `06ddd57`, PR #11; tasks.md 32/34 ticked).
  Playback fix verified live (the failing URL fetch now returns 200 audio/wav). REMAINING:
  T033 — the Gate G5 evidence run (needs 2+ fresh live sessions so the guidance row populates;
  write `gate-decision.md`), and in-browser audible playback confirmation on a human session.
- **RDS migrations now run as a one-off ECS task** (in-VPC; no laptop SG dance):
  `aws ecs run-task` on the worker task def with command override `python -m src.db_migrate`
  — db_migrate honors DB_SECRET_ARN (passwordless DSN + Secrets Manager provider).

### Prior feature: 007 — Pipecat Library Adoption (COMPLETE, deployed)

Specs in `specs/007-pipecat-adoption/` (`gate-decision.md` has the SC-001 PASS). G1-G6 are the
proven merged/shipped bases. G1 (`specs/001-voice-interview-loop/`) is the latency foundation,
and its `contracts/metrics-contract.md` + `harness/aggregate.py` are reused verbatim as the
SC-001 gate.

- **Constitution**: `.specify/memory/constitution.md` (Principles I-VII; I/II/III are NON-NEGOTIABLE).
- **Goal of F007**: migrate the voice-worker's hand-written aiortc turn loop onto the **Pipecat
  library** (self-hosted `SmallWebRTCTransport`, NOT Pipecat Cloud) inside the SAME ECS Fargate worker,
  preserving every external behavior + the proven gates, while gaining true voice-activated barge-in,
  robust on-audio Silero VAD (replacing the brittle DTX watchdog), and built-in metrics. Exit: SC-001
  re-PROVEN on the Pipecat loop (p50<1000/p95<1500, hard gate p50<=1200) on a recording-ON session.
- **Locked decisions (do not re-litigate)**: full pipeline migration; A/B the latency strategy
  (custom `LeadClauseProcessor` vs native LLM-on-critical-path) via the immutable aggregate gate,
  default locked to the passing arm with the better margin; structured as Spec-Kit feature 007.
- **Latency rule (load-bearing)**: the lead-clause (speak a backchannel the instant the student stops
  while the LLM streams behind it) keeps the LLM OFF the response_gap clock — it has NO native Pipecat
  equivalent, so it is a custom `FrameProcessor`. The frame-graph `orchestration_ms` (0 on the hand
  loop) is the #1 risk; the live SC-001 run retires it.
- **Committed design**: custom FrameProcessors carry the load-bearing logic (LeadClause, Deadline,
  LatencyObserver→same `turn_latency` contract, Recording→G6 taps off the gap clock, TurnGate→ptt +
  barge-in); a `ReplyGenerator→LLMService` adapter reuses the tuned `bedrock_direct`/`agentcore`
  providers + persona grounding; `server.py /offer` keeps the Bearer-token + JSON `{sdp,type}` contract
  (SPA unchanged); Silero VAD on the user aggregator. NO schema change; topology unchanged (direct UDP
  media, RDS+S3 only PII home). Pipecat Cloud FORBIDDEN (would move PII off-topology + reopen latency).
- **Build state**: F007 COMPLETE — merged (PR #1) + deployed; SC-001 PASSED live (lead-clause p50
  290ms vs native 1504ms; `gate-decision.md`). T033 executed: the legacy loop (`pipeline.py`,
  `transport_webrtc.py`, `stt_deepgram.py`) and its tests are retired; the Pipecat image is the
  canonical `Dockerfile`/`requirements-lock.txt`; rollback is the retained pre-007 ECR image tag.
  `tts_deepgram.py` lives on in `harness/` (the harness's student-speech synthesizer only).
- **Verified Pipecat 1.3 API** (introspected, in `pipecat-api-notes.md`): custom LLM overrides
  `_process_context` (no `run_llm`); `InterruptionFrame` (not `StartInterruptionFrame`);
  `PipelineWorker`/`WorkerRunner` (not the deprecated `PipelineTask`/`PipelineRunner`); VAD on
  `LLMUserAggregatorParams.vad_analyzer`; Deepgram `Settings(...)` not `live_options=`/`voice=`.
- **Python**: Pipecat venv in `voice-worker/.venv-pipecat` (Python 3.13) — the primary test venv.
  Run pytest in BOTH `.venv-pipecat` and `.venv` before merging (conftest stays lazy-import safe).
  No emoji in generated code.
