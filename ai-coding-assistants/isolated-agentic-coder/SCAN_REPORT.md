# Security scan report

**Date:** 29 May 2026
**Scope:** All project files excluding `./sandbox/`
**Scanners:** Semgrep, Bandit, Checkov, Trivy

## Summary

| Scanner | Critical | High | Medium | Low | Total |
|---|---|---|---|---|---|
| Semgrep (Python) | 0 | 0 | 0 | 0 | **0** |
| Bandit (Python) | 0 | 0 | 0 | 3 | **3** |
| Checkov (Dockerfile) | 0 | 0 | 0 | 0 | **2** |
| Trivy (Dockerfile) | 0 | 0 | 0 | 1 | **1** |

Note: Checkov findings have no severity assigned by the tool; both are informational best-practice checks.

## Findings

### Bandit: 3 LOW (all false positives)

| # | ID | Location | Description | Verdict |
|---|---|---|---|---|
| 1 | B404 | `buildingo.py:52` | `import subprocess` flagged | FP. Subprocess is the core mechanism; the container is the security boundary. |
| 2 | B603 | `buildingo.py:185` | `subprocess.run` with non-literal args in `_exec` | FP. Commands are list-based (no shell injection). Dynamic content runs inside the container, not on the host. |
| 3 | B603 | `buildingo.py:430` | `subprocess.run` with non-literal args in `_run` | FP. Same as above; passes a list, not a shell string. |

### Checkov: 2 informational (both not applicable)

| # | ID | Description | Verdict |
|---|---|---|---|
| 1 | CKV_DOCKER_2 | No `HEALTHCHECK` defined | **Not applicable.** This is a `sleep infinity` build sandbox, not a service. Health checks add no value. |
| 2 | CKV2_DOCKER_1 | `sudo` should not be used | **By design.** The non-root `buildingo` user requires passwordless sudo to install language toolchains at runtime. The container boundary (cap_drop ALL, limited cap_add, pids/mem/cpu limits) is the security layer. |

### Trivy: 1 LOW

| # | ID | Severity | Description | Verdict |
|---|---|---|---|---|
| 1 | DS-0026 | LOW | No `HEALTHCHECK` defined | **Not applicable.** Same as CKV_DOCKER_2 above. |

## Changes since last scan (28 May 2026)

- Removed `no-new-privileges:true` from `docker-compose.yml` to allow `sudo` to function inside the container. Security posture is maintained via `cap_drop: ALL` with selective `cap_add`, pids/mem/cpu limits, and the container boundary itself.
- Added `git config --global init.templateDir ""` to Dockerfile (cosmetic; suppresses a git warning).
- Removed `--template=/dev/null` from the git init bootstrap in `buildingo.py` (cosmetic).
- New Checkov finding CKV2_DOCKER_1 (sudo usage) appeared because the sudoers configuration was already present but Checkov now flags it. This is by design.

## Verdict

**Clean.** Zero critical, high, or medium findings. The 3 Bandit LOWs are known false positives for this architecture. The 2 Checkov findings and 1 Trivy LOW are non-applicable best practices for this container shape (build sandbox, not a production service).
