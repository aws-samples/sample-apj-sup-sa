# Docker & Container Analysis - Detailed Steps

## Purpose
Analyze Dockerfile and container configuration for security, size, and efficiency.

---

## Step 1: Dockerfile Best Practices

| Check | Detection | Severity | Fix |
|-------|-----------|----------|-----|
| Running as root | No `USER` instruction | 🟠 High | Add non-root USER |
| Outdated base image | Old tag (e.g., node:14) | 🟡 Medium | Update to latest LTS |
| No multi-stage build | Single FROM, includes build tools in final | 🟡 Medium | Add build stage |
| Secrets in ENV/ARG | `ENV PASSWORD=` or `ARG SECRET=` | 🔴 Critical | Use secrets mount |
| No .dockerignore | Missing file | 🟡 Medium | Add .dockerignore |
| COPY . . without ignore | Copies node_modules, .git, etc. | 🟡 Medium | Use .dockerignore |
| No health check | Missing HEALTHCHECK | 🟡 Medium | Add HEALTHCHECK |
| Latest tag | `FROM node:latest` | 🟡 Medium | Pin specific version |
| Unnecessary packages | `apt-get install` without `--no-install-recommends` | 🔵 Info | Add flag |
| Missing cache cleanup | No `rm -rf /var/lib/apt/lists/*` | 🔵 Info | Clean in same layer |
| Inefficient layer order | COPY source before dependencies | 🟡 Medium | Copy package files first |

---

## Step 2: Image Size Analysis

```bash
# Check base image sizes
# node:18 → ~900MB | node:18-slim → ~200MB | node:18-alpine → ~110MB
# python:3.11 → ~900MB | python:3.11-slim → ~120MB | python:3.11-alpine → ~50MB
```

### Size Recommendations:
| Current Base | Recommended | Savings |
|-------------|-------------|---------|
| `node:18` | `node:18-alpine` | ~790MB |
| `python:3.11` | `python:3.11-slim` | ~780MB |
| `ubuntu:22.04` | `debian:bookworm-slim` | ~100MB |
| `openjdk:17` | `eclipse-temurin:17-jre-alpine` | ~400MB |

---

## Step 3: Docker Compose Analysis

| Check | Detection | Severity |
|-------|-----------|----------|
| No restart policy | Missing `restart:` | 🟡 Medium |
| Hardcoded ports | Host ports that may conflict | 🔵 Info |
| No resource limits | Missing `deploy.resources.limits` | 🟡 Medium |
| Privileged mode | `privileged: true` | 🔴 Critical |
| Host network | `network_mode: host` | 🟠 High |
| Secrets in environment | Passwords in `environment:` block | 🟠 High |
| No healthcheck | Missing `healthcheck:` | 🟡 Medium |
| Missing depends_on | Service ordering not declared | 🔵 Info |

---

## Step 4: Generate Report

```markdown
## 🐳 Container Analysis Report

### Dockerfile Assessment
| File | Base Image | Size (est) | Security | Efficiency | Score |
|------|-----------|-----------|----------|-----------|-------|
| Dockerfile | {image} | {size} | {🟢/🟡/🔴} | {🟢/🟡/🔴} | {n}/10 |

### Findings
| # | Issue | File | Line | Severity | Fix |
|---|-------|------|------|----------|-----|
| 1 | Running as root | Dockerfile | — | 🟠 | `USER node` after setup |
| 2 | No multi-stage | Dockerfile | — | 🟡 | Add build + runtime stages |

### Size Optimization Opportunities
| Action | Current | Optimized | Savings |
|--------|---------|-----------|---------|
| Switch to alpine | {size} | {size} | {diff} |
| Multi-stage build | {size} | {size} | {diff} |
| Remove dev deps | {size} | {size} | {diff} |
```

---

## Completion
Output to `dep-analysis-docs/analysis/docker-analysis.md`
