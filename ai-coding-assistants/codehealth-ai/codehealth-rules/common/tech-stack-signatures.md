# Tech Stack Detection Signatures

## Detection Strategy

Tech stack detection uses a multi-signal approach combining file presence,
file content analysis, and cross-referencing for confidence scoring.

## Signal Priority

1. **Primary Indicators** (High Confidence): Package manager manifests, build tool configs
2. **Secondary Indicators** (Medium Confidence): Framework-specific files, CI configs
3. **Tertiary Indicators** (Low Confidence): File extensions, directory naming conventions

---

## Language Detection Signatures

### JavaScript / TypeScript
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `package.json` | High |
| Primary | `tsconfig.json` | High (TypeScript) |
| Secondary | `.eslintrc*`, `.prettierrc*` | Medium |
| Secondary | `webpack.config.*`, `vite.config.*`, `rollup.config.*` | Medium |
| Tertiary | `*.js`, `*.ts`, `*.jsx`, `*.tsx` files | Low |

### Python
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `requirements.txt` | High |
| Primary | `pyproject.toml` | High |
| Primary | `Pipfile` | High |
| Primary | `setup.py` / `setup.cfg` | High |
| Secondary | `tox.ini`, `pytest.ini`, `.flake8` | Medium |
| Secondary | `manage.py` (Django) | Medium |
| Tertiary | `*.py` files | Low |

### Java / Kotlin
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `pom.xml` | High (Maven) |
| Primary | `build.gradle` / `build.gradle.kts` | High (Gradle) |
| Primary | `settings.gradle` / `settings.gradle.kts` | High (Gradle) |
| Secondary | `mvnw`, `gradlew` | Medium |
| Secondary | `src/main/java/`, `src/main/kotlin/` | Medium |
| Tertiary | `*.java`, `*.kt` files | Low |

### Go
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `go.mod` | High |
| Primary | `go.sum` | High |
| Secondary | `Makefile` with go commands | Medium |
| Tertiary | `*.go` files | Low |

### Rust
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `Cargo.toml` | High |
| Primary | `Cargo.lock` | High |
| Secondary | `rust-toolchain.toml` | Medium |
| Tertiary | `*.rs` files | Low |

### .NET / C#
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `*.csproj` | High |
| Primary | `*.sln` | High |
| Primary | `Directory.Build.props` | High |
| Secondary | `nuget.config`, `global.json` | Medium |
| Tertiary | `*.cs`, `*.fs` files | Low |

### Ruby
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `Gemfile` | High |
| Primary | `*.gemspec` | High |
| Secondary | `Rakefile`, `.ruby-version` | Medium |
| Tertiary | `*.rb` files | Low |

### PHP
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `composer.json` | High |
| Secondary | `artisan` (Laravel), `bin/console` (Symfony) | Medium |
| Tertiary | `*.php` files | Low |

### Swift / iOS
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `Package.swift` | High |
| Primary | `Podfile` | High |
| Primary | `*.xcodeproj` / `*.xcworkspace` | High |
| Tertiary | `*.swift` files | Low |

### Dart / Flutter
| Signal | File/Pattern | Confidence |
|--------|-------------|-----------|
| Primary | `pubspec.yaml` | High |
| Secondary | `analysis_options.yaml` | Medium |
| Tertiary | `*.dart` files | Low |

---

## Framework Detection (Content-Based)

After language detection, parse manifest contents for framework indicators:

### Node.js Frameworks
| Framework | Detection Signal |
|-----------|-----------------|
| React | `"react"` in dependencies |
| Next.js | `"next"` in dependencies |
| Angular | `"@angular/core"` in dependencies |
| Vue.js | `"vue"` in dependencies |
| Nuxt | `"nuxt"` in dependencies |
| Svelte | `"svelte"` in dependencies |
| Express | `"express"` in dependencies |
| Fastify | `"fastify"` in dependencies |
| NestJS | `"@nestjs/core"` in dependencies |
| Hono | `"hono"` in dependencies |
| Remix | `"@remix-run/node"` in dependencies |

### Python Frameworks
| Framework | Detection Signal |
|-----------|-----------------|
| Django | `django` in requirements |
| Flask | `flask` in requirements |
| FastAPI | `fastapi` in requirements |
| Starlette | `starlette` in requirements |
| Celery | `celery` in requirements |
| SQLAlchemy | `sqlalchemy` in requirements |

### Java Frameworks
| Framework | Detection Signal |
|-----------|-----------------|
| Spring Boot | `spring-boot-starter` in pom/gradle |
| Quarkus | `quarkus` in pom/gradle |
| Micronaut | `micronaut` in pom/gradle |
| Jakarta EE | `jakarta.*` in pom/gradle |

---

## Infrastructure & Tooling Detection

| Category | Signal | Tool Detected |
|----------|--------|--------------|
| Containers | `Dockerfile` | Docker |
| Containers | `docker-compose.yml` | Docker Compose |
| Orchestration | `k8s/`, `kubernetes/` | Kubernetes |
| Orchestration | `helm/`, `Chart.yaml` | Helm |
| IaC | `*.tf` files | Terraform |
| IaC | `cdk.json` | AWS CDK |
| IaC | `Pulumi.yaml` | Pulumi |
| IaC | `serverless.yml` | Serverless Framework |
| IaC | `template.yaml` (SAM) | AWS SAM |
| IaC | `cloudformation/` | AWS CloudFormation |
| CI/CD | `.github/workflows/` | GitHub Actions |
| CI/CD | `.gitlab-ci.yml` | GitLab CI |
| CI/CD | `Jenkinsfile` | Jenkins |
| CI/CD | `.circleci/` | CircleCI |
| CI/CD | `buildspec.yml` | AWS CodeBuild |

---

## Monorepo Detection

| Tool | Detection Signal |
|------|-----------------|
| Lerna | `lerna.json` |
| Nx | `nx.json` |
| Turborepo | `turbo.json` |
| pnpm Workspaces | `pnpm-workspace.yaml` |
| Yarn Workspaces | `"workspaces"` in root package.json |
| npm Workspaces | `"workspaces"` in root package.json |
| Gradle Multi-Project | `settings.gradle` with `include` |
| Maven Multi-Module | `<modules>` in parent pom.xml |

---

## Confidence Scoring Algorithm

```
confidence = 0

For each signal found:
  if signal.level == "Primary":   confidence += 30
  if signal.level == "Secondary": confidence += 15
  if signal.level == "Tertiary":  confidence += 5

Final classification:
  confidence >= 60  → HIGH    (proceed automatically)
  confidence 30-59  → MEDIUM  (present to user, ask confirmation)
  confidence < 30   → LOW     (flag uncertainty, require user input)
```

## Multi-Stack Projects

Many projects use multiple tech stacks. Report ALL detected stacks, ordered by
confidence. Common patterns:

- Frontend (React/Vue) + Backend (Node.js/Python/Java) + IaC (Terraform/CDK)
- Monorepo with mixed languages across packages
- Microservices with different languages per service

For each detected stack, run the full dependency analysis pipeline independently,
then merge findings in the final report.
