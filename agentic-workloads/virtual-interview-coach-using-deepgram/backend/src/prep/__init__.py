"""Session-prep blueprint assembly (Feature 002 / Gate G2).

Off the response_gap clock, before the turn clock starts. Assembles the ordered question
plan by a pure DB operation: a filtered SQL query over approved archetypes + a pgvector
similarity rank against the job-description embedding. ZERO live LLM on the selection path
(SC-003). See contracts/session-prep-contract.md.

  retrieval.py  (T015) -- pgvector IVFFlat filtered + ranked query (zero LLM)
  blueprint.py  (T016) -- embed JD once, derive competencies + ordered plan, niche fallback
"""
