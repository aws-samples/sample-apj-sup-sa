# Environment Configuration Hygiene - Detailed Steps

## Purpose
Ensure environment configuration is well-managed, documented, and safe.

---

## Step 1: Discover Configuration Sources

```bash
# Find all configuration files
find . -name ".env*" -o -name "*.config.*" -o -name "config.*" \
  -o -name "settings.*" -o -name "*.properties" | grep -v node_modules | grep -v .git
```

### Common Config Locations:
| Framework | Config Files |
|-----------|-------------|
| Node.js | `.env`, `config/`, `src/config.ts` |
| Python/Django | `.env`, `settings.py`, `config.py` |
| Java/Spring | `application.yml`, `application.properties` |
| .NET | `appsettings.json`, `appsettings.{env}.json` |
| Go | `.env`, `config.yaml`, embedded config |

---

## Step 2: Configuration Hygiene Checks

| Check | What to Look For | Severity |
|-------|-----------------|----------|
| .env.example exists | Template for required env vars | 🟡 if missing |
| All vars documented | Each env var has comment/description | 🔵 Info |
| Safe defaults | Dev defaults don't point to prod | 🟠 if unsafe |
| Type validation | Config values validated at startup | 🟡 if missing |
| Required vars checked | App fails fast if critical config missing | 🟡 if missing |
| No env sprawl | Config in ≤3 sources (not scattered) | 🟡 if sprawled |
| Environment separation | Different configs for dev/staging/prod | 🟡 if missing |

---

## Step 3: Configuration Sprawl Assessment

Count how many places configuration lives:
- .env files
- Config modules/files
- Hardcoded in source
- CI/CD variables
- Docker/K8s configs
- IaC variables

If > 5 sources: flag as "configuration sprawl" 🟡

---

## Step 4: Generate Report

```markdown
## ⚙️ Environment Configuration Report

### Configuration Sources
| Source | Type | Vars Count | Documented |
|--------|------|-----------|-----------|
| .env | Runtime | {n} | {yes/no} |
| config/index.ts | Build-time | {n} | {yes/no} |
| docker-compose | Container | {n} | {yes/no} |

### Hygiene Score: {score}/100

### Issues
| # | Issue | Severity | Recommendation |
|---|-------|----------|---------------|
| 1 | Missing .env.example | 🟡 | Create template with all required vars |
| 2 | No type validation | 🟡 | Add zod/joi config schema |
| 3 | Production URL in dev default | 🟠 | Change default to localhost |

### Recommended Configuration Architecture
{Framework-specific recommendations for config management}
```

---

## Completion
Output to `dep-analysis-docs/analysis/env-config.md`
