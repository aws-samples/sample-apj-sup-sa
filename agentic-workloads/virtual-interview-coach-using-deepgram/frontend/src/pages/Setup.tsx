// Pre-session setup screen (T021, restyled in F007) — resume upload + parse-back confirm, job scope,
// difficulty pick, consent. Styled with the shared design system (theme.css) to match the prototype.
// Drives the US1 happy path: consent -> upload -> confirm facts -> job + difficulty + duration -> start.

import { useEffect, useState } from "react";
import ResumeConfirm from "../components/ResumeConfirm";
import DifficultyPicker from "../components/DifficultyPicker";
import DurationPicker from "../components/DurationPicker";
import { AppBar } from "./Session";
import {
  confirmResume,
  emptyFacts,
  getResume,
  scrapeJob,
  setConsent,
  uploadResume,
  type ParseStatus,
  type ParsedFacts,
} from "../lib/setupApi";
import {
  createSession,
  type CreateSessionResponse,
  type Difficulty,
  type DurationMinutes,
} from "../lib/sessionApi";

// Resume retention is fixed at 30 days (no longer a user choice — item 2). Kept as a named constant
// so the consent call still records the policy explicitly.
const RETENTION_DAYS = 30;

// Demo resume (test convenience): a computer-engineering grad with ~3-4 years of cloud/infra
// experience, so the prefilled Senior Cloud Engineer JD lands on a believable, well-matched candidate.
// Used to prefill the manual-entry path; both fields stay fully editable. Mirrors the parsed-facts
// shape (name/summary/skills/experience{title,organization,duration,highlights}/education{...}).
const DEMO_RESUME_FACTS: ParsedFacts = {
  name: "Jordan Tan",
  summary:
    "Computer Engineering graduate and cloud engineer with ~4 years building and operating " +
    "production AWS infrastructure. Strong in infrastructure as code (Terraform), CI/CD, and " +
    "containerized workloads; comfortable owning reliability, security, and cost across multi-account " +
    "environments.",
  skills: [
    "AWS", "Terraform", "AWS CloudFormation", "Docker", "Kubernetes (EKS)", "CI/CD",
    "GitHub Actions", "Python", "Bash", "Linux", "VPC / networking", "IAM",
    "CloudWatch", "Prometheus", "Grafana", "PostgreSQL (RDS)", "Lambda", "Infrastructure as Code",
  ],
  experience: [
    {
      title: "Cloud Engineer",
      organization: "Lumen Payments (fintech scale-up)",
      duration: "2023 - present",
      highlights: [
        "Owned the Terraform monorepo for a multi-account AWS estate (40+ services), introducing reusable modules and a peer-review gate that cut environment drift to near zero.",
        "Built zero-downtime blue/green deploys on ECS + a GitHub Actions CI/CD pipeline, taking mean deploy time from ~25 min to under 6 and adding automatic rollback on health-check failure.",
        "Led the migration of a stateful service onto EKS with least-privilege IAM and network policies; cut compute cost ~30% via right-sizing and spot capacity.",
        "On-call for production: instrumented CloudWatch + Grafana dashboards and alerting that caught a memory-leak regression before customer impact.",
      ],
    },
    {
      title: "DevOps / Platform Engineer",
      organization: "Northwind Logistics",
      duration: "2021 - 2023",
      highlights: [
        "Codified legacy click-ops infrastructure into CloudFormation/CDK, making environments reproducible and auditable for a SOC 2 effort.",
        "Stood up centralized logging and secrets management (SSM Parameter Store + KMS), removing plaintext credentials from CI.",
        "Automated nightly RDS backups and a tested restore runbook; mentored two junior engineers on Terraform and code review.",
      ],
    },
    {
      title: "Software Engineering Intern",
      organization: "University Research Computing",
      duration: "Summer 2020",
      highlights: [
        "Built a Python tool to provision short-lived AWS sandbox accounts for student projects, with budget guardrails and auto-teardown.",
      ],
    },
  ],
  education: [
    {
      qualification: "BEng Computer Engineering",
      institution: "National University of Singapore",
      year: "2021",
    },
    {
      qualification: "AWS Certified Solutions Architect - Associate",
      institution: "Amazon Web Services",
      year: "2022",
    },
  ],
};

// Demo prefill for the Role step so testing does not require typing a job each run (test
// convenience only — both fields stay editable). Chosen to match the synthetic backend-SWE persona
// so the blueprint grounds sensibly against the seeded bank.
const DEMO_JOB_TITLE = "Senior Cloud Engineer";
const DEMO_JOB_DESCRIPTION =
  "We are hiring a Senior Cloud Engineer to design, build, and operate scalable, secure, and " +
  "highly-available infrastructure on AWS. You will own infrastructure as code (Terraform and/or " +
  "AWS CloudFormation/CDK), build and harden CI/CD pipelines, and drive reliability through " +
  "observability, automated testing of infrastructure, and incident response. Core responsibilities: " +
  "architect multi-account AWS environments (VPC, IAM, ECS/EKS, Lambda, RDS, S3); codify everything " +
  "as reusable IaC modules with peer review; automate deployments with zero-downtime/rollback " +
  "strategies; manage networking, secrets, and least-privilege access; and optimize cost and " +
  "performance at scale. Requirements: 4+ years building production cloud systems on AWS; deep, " +
  "hands-on Terraform (modules, state, workspaces) and at least one of CloudFormation/CDK; strong " +
  "Linux, containers (Docker, Kubernetes), and scripting (Python/Bash); experience with monitoring " +
  "(CloudWatch, Prometheus/Grafana) and debugging production incidents; and the communication skills " +
  "to mentor engineers and explain trade-offs to non-technical stakeholders. Kafka/event-driven " +
  "pipelines, data platforms, and security/compliance experience are a plus.";

// The flow steps. Each completes before the next is shown so the student is never blocked on an
// out-of-order requirement (consent gates everything; a confirmed resume grounds the session).
// "job" captures the role (title/description, optionally scraped from a URL); "tuning" picks the
// difficulty + interview length and starts the session.
type Step = "consent" | "resume" | "confirm" | "job" | "tuning";

interface Props {
  accessToken: string;
  // Called with the created personalized session once setup completes; the parent then starts media
  // (reusing the unchanged G1 media plane). Setup itself never touches WebRTC.
  onReady: (session: CreateSessionResponse) => void;
}

export default function Setup({ accessToken, onReady }: Props) {
  const [step, setStep] = useState<Step>("consent");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // consent — retention is fixed at 30 days (item 2), no longer asked.

  // resume parse-back
  const [facts, setFacts] = useState<ParsedFacts>(emptyFacts());
  const [parseStatus, setParseStatus] = useState<ParseStatus>("parsed");
  const [confirmed, setConfirmed] = useState(false);

  // job scope — prefilled with a representative role so the demo Role step is ready to submit
  // without typing (test convenience only; the student can overwrite both fields).
  const [jobTitle, setJobTitle] = useState(DEMO_JOB_TITLE);
  const [jobDescription, setJobDescription] = useState(DEMO_JOB_DESCRIPTION);
  // Optional: paste a job-posting link and we scrape the title/description into the fields above
  // (mirrors the resume upload-and-parse path). The URL itself is never stored.
  const [jobUrl, setJobUrl] = useState("");
  const [scraping, setScraping] = useState(false);
  const [scrapeNote, setScrapeNote] = useState("");
  const [difficulty, setDifficulty] = useState<Difficulty>("moderate");
  const [durationMinutes, setDurationMinutes] = useState<DurationMinutes>(10);
  // F006 (G6): per-session recording choice — on by default so the student can replay answers; they
  // can opt out here and no audio is stored for the session (transcript + report still produced).
  const [recordAudio, setRecordAudio] = useState(true);

  // Returning student: once consent is in place, offer reuse of a previously confirmed resume (FR-202).
  const [reuse, setReuse] = useState<{ confirmedAt: string } | null>(null);
  useEffect(() => {
    if (step !== "resume") return;
    let cancelled = false;
    getResume(accessToken)
      .then((r) => {
        if (!cancelled && r && r.resume_confirmed_at) {
          setFacts(r.parsed_facts);
          setReuse({ confirmedAt: r.resume_confirmed_at });
        }
      })
      .catch(() => {
        /* no stored resume / not reachable — fall through to upload */
      });
    return () => {
      cancelled = true;
    };
  }, [step, accessToken]);

  async function submitConsent(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await setConsent(accessToken, true, RETENTION_DAYS);
      setStep("resume");
    } catch (err) {
      setError(messageOf(err, "Could not save your consent choice."));
    } finally {
      setBusy(false);
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const res = await uploadResume(accessToken, file);
      setFacts(res.parsed_facts);
      setParseStatus(res.parse_status);
      setStep("confirm");
    } catch (err) {
      setError(messageOf(err, "Could not upload your resume."));
    } finally {
      setBusy(false);
    }
  }

  // Skip the upload entirely and go straight to manual entry (SC-006) — also the path when a student
  // has no file handy. Prefilled with the demo resume for test convenience; every field stays editable
  // (clear them for a blank manual entry).
  function enterManually() {
    setFacts(DEMO_RESUME_FACTS);
    setParseStatus("parsed");
    setStep("confirm");
  }

  function reuseStored() {
    // The stored facts are already confirmed; let the student re-confirm "still accurate?" then move on.
    setParseStatus("parsed");
    setStep("confirm");
  }

  async function onConfirmFacts(corrected: ParsedFacts, manualEntry: boolean) {
    setBusy(true);
    setError("");
    try {
      await confirmResume(accessToken, corrected, manualEntry);
      setFacts(corrected);
      setConfirmed(true);
      setStep("job");
    } catch (err) {
      setError(messageOf(err, "Could not save your confirmed details."));
    } finally {
      setBusy(false);
    }
  }

  // Scrape a pasted job-posting URL into the title/description fields (mirrors resume parse-back).
  // On a "partial" result the model was unavailable and we returned raw page text — nudge the student
  // to trim it. The URL is never persisted.
  async function onScrapeJob() {
    if (!jobUrl.trim()) {
      setError("Paste a job posting link first, or just type the description below.");
      return;
    }
    setScraping(true);
    setError("");
    setScrapeNote("");
    try {
      const res = await scrapeJob(accessToken, jobUrl.trim());
      if (res.job_title) setJobTitle(res.job_title);
      if (res.job_description) setJobDescription(res.job_description);
      setScrapeNote(
        res.scrape_status === "scraped"
          ? "Imported from the link — review and edit below before continuing."
          : "We pulled the page text but couldn't tidy it automatically. Please trim it below."
      );
    } catch (err) {
      setError(messageOf(err, "We couldn't read that link. Please paste the description instead."));
    } finally {
      setScraping(false);
    }
  }

  // The role step now advances to difficulty/length tuning rather than starting the session.
  function goToTuning(e: React.FormEvent) {
    e.preventDefault();
    if (!jobDescription.trim()) {
      setError("Please paste the job description so we can tailor your interview.");
      return;
    }
    setError("");
    setStep("tuning");
  }

  // Reverse navigation for the Back control. Mirrors the forward order; never goes before consent.
  function goBack() {
    setError("");
    setScrapeNote("");
    setStep((s) =>
      s === "tuning" ? "job" : s === "job" ? "confirm" : s === "confirm" ? "resume" : "consent"
    );
  }

  async function startSession(e: React.FormEvent) {
    e.preventDefault();
    if (!jobDescription.trim()) {
      setError("Please paste the job description so we can tailor your interview.");
      setStep("job");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const session = await createSession(accessToken, {
        jobTitle: jobTitle.trim(),
        jobDescription: jobDescription.trim(),
        difficulty,
        durationMinutes,
        recordAudio,
      });
      // Carry the chosen role + difficulty into the live screen's "<difficulty> · <role>" pill
      // (the backend response doesn't echo the title), and the consent choice for Privacy.
      onReady({ ...session, jobTitle: jobTitle.trim(), difficulty, recordAudio });
    } catch (err) {
      setError(messageOf(err, "We couldn't start your session. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  const stepPill: Record<Step, string> = {
    consent: "Set up · Step 1 of 5 · Consent",
    resume: "Set up · Step 2 of 5 · Resume",
    confirm: "Set up · Step 3 of 5 · Confirm",
    job: "Set up · Step 4 of 5 · The role",
    tuning: "Set up · Step 5 of 5 · Difficulty & length",
  };

  return (
    <div className="app">
      <AppBar right={<span className="pill teal">{stepPill[step]}</span>} />
      <div className="pad">
        <div className="center-wrap">
          <Progress step={step} />

          {step !== "consent" && (
            <button type="button" className="backbtn" onClick={goBack} style={{ marginBottom: 18 }}>
              <span className="arrow" aria-hidden="true">←</span> Back
            </button>
          )}

          {step === "consent" && (
            <form onSubmit={submitConsent}>
              <h2 className="title">Before we start 🔒</h2>
              <p className="sub">
                To tailor your practice interview we store your resume and the job description securely,
                encrypted, and delete them automatically after 30 days. You can delete everything at any time.
              </p>
              <div className="card">
                <div className="info-row">
                  <span className="ico">🛡️</span>
                  <div className="rt">
                    <b>What we keep, and for how long</b>
                    Your resume and the job description, encrypted at rest in one region, kept for{" "}
                    <b style={{ display: "inline" }}>30 days</b> then auto-deleted. Used only to personalize your interview.
                  </div>
                </div>
              </div>
              <label className="card choice">
                <div className="info-row">
                  <input
                    type="checkbox"
                    checked={recordAudio}
                    onChange={(e) => setRecordAudio(e.target.checked)}
                  />
                  <span className="rt">
                    <b>Record my answers so I can play them back 🎧</b>
                    We'll securely store the audio of this interview (encrypted, auto-deleted after 30 days)
                    so you can listen to your answers afterward. Uncheck to practice without recording — your
                    transcript and report are still produced either way.
                  </span>
                </div>
              </label>
              <button type="submit" disabled={busy} className="btn primary lg" style={{ marginTop: 24 }}>
                {busy ? "Saving…" : "I consent — continue →"}
              </button>
            </form>
          )}

          {step === "resume" && (
            <div>
              <h2 className="title">Upload your resume</h2>
              <p className="sub">
                Upload your resume (PDF, DOCX, or TXT) and we'll pull out the key facts for you to
                confirm — we tailor every question to these.
              </p>
              {reuse && (
                <div className="notice">
                  <p style={{ margin: "0 0 8px" }}>We found a resume you confirmed earlier. Reuse it, or upload a new one.</p>
                  <button type="button" className="btn ghost sm" onClick={reuseStored}>Reuse my saved resume</button>
                </div>
              )}
              <div className="card">
                <div className="field" style={{ marginBottom: 8 }}>
                  <label>Resume file</label>
                  <input type="file" accept=".pdf,.docx,.txt" onChange={onFile} disabled={busy} />
                </div>
                {busy && <p className="hint">Reading your resume…</p>}
                <button type="button" className="linkbtn" onClick={enterManually}>Or enter my details manually</button>
              </div>
            </div>
          )}

          {step === "confirm" && (
            <>
              <h2 className="title">Confirm your details</h2>
              <ResumeConfirm
                initialFacts={facts}
                parseStatus={parseStatus}
                busy={busy}
                onConfirm={onConfirmFacts}
              />
            </>
          )}

          {step === "job" && (
            <form onSubmit={goToTuning}>
              <h2 className="title">Now tell us about the role</h2>
              <p className="sub">
                {confirmed ? "Great — your details are saved. " : ""}
                I'll tailor every question to your confirmed resume and this job. The more real it
                is, the more useful your practice.
              </p>
              <div className="card" style={{ marginBottom: 16 }}>
                <div className="field">
                  <label>Job posting link <span style={{ fontWeight: 400, color: "var(--ink-faint)" }}>(optional)</span></label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      type="url"
                      value={jobUrl}
                      onChange={(e) => setJobUrl(e.target.value)}
                      placeholder="https://company.com/careers/backend-engineer"
                      disabled={scraping || busy}
                      style={{ flex: 1 }}
                    />
                    <button
                      type="button"
                      className="btn ghost"
                      onClick={onScrapeJob}
                      disabled={scraping || busy}
                      style={{ whiteSpace: "nowrap" }}
                    >
                      {scraping ? "Importing…" : "Import from link"}
                    </button>
                  </div>
                  <p className="hint">
                    Paste a link to the job and we'll pull in the title and description for you to
                    review — or just type them in below. We don't store the link.
                  </p>
                  {scrapeNote && <p className="hint" style={{ color: "var(--primary-deep)" }}>{scrapeNote}</p>}
                </div>
                <div className="field">
                  <label>Job title</label>
                  <input
                    value={jobTitle}
                    onChange={(e) => setJobTitle(e.target.value)}
                    placeholder="e.g. Backend Software Engineer"
                  />
                </div>
                <div className="field" style={{ marginBottom: 0 }}>
                  <label>Job description</label>
                  <textarea
                    style={{ minHeight: 150 }}
                    value={jobDescription}
                    onChange={(e) => setJobDescription(e.target.value)}
                    placeholder="Paste the full job description here."
                    required
                  />
                </div>
              </div>
              <button type="submit" disabled={busy || scraping} className="btn primary lg" style={{ marginTop: 8 }}>
                Continue →
              </button>
            </form>
          )}

          {step === "tuning" && (
            <form onSubmit={startSession}>
              <h2 className="title">Difficulty & length</h2>
              <p className="sub">
                Choose how challenging the interview feels and how long it runs. You can go back to
                adjust the role anytime.
              </p>
              <div className="field"><label>Difficulty</label></div>
              <DifficultyPicker value={difficulty} onChange={setDifficulty} disabled={busy} />
              <div className="field"><label>Interview length</label></div>
              <DurationPicker value={durationMinutes} onChange={setDurationMinutes} disabled={busy} />
              <button type="submit" disabled={busy} className="btn accent lg" style={{ marginTop: 20 }}>
                {busy
                  ? "Preparing your interview…"
                  : `Start interview — ${difficulty[0].toUpperCase()}${difficulty.slice(1)}, ${durationMinutes} min →`}
              </button>
              <p className="hint" style={{ textAlign: "center", marginTop: 12 }}>
                We'll do a quick mic check first, then begin.
              </p>
            </form>
          )}

          {error && <p className="alert" role="alert" style={{ marginTop: 14 }}>{error}</p>}
        </div>
      </div>
    </div>
  );
}

function Progress({ step }: { step: Step }) {
  const steps: { key: Step; label: string }[] = [
    { key: "consent", label: "Consent" },
    { key: "resume", label: "Resume" },
    { key: "confirm", label: "Confirm" },
    { key: "job", label: "Role" },
    { key: "tuning", label: "Tune" },
  ];
  const idx = steps.findIndex((s) => s.key === step);
  return (
    <ol className="progress">
      {steps.map((s, i) => (
        <li key={s.key} className={i === idx ? "current" : i < idx ? "done" : ""}>
          {i < idx ? "✓ " : ""}{s.label}
        </li>
      ))}
    </ol>
  );
}

function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

