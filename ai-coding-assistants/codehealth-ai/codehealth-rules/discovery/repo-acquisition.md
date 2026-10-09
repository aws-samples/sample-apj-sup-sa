# Repository Acquisition - Detailed Steps

## Purpose
Acquire the target repository for analysis, whether local, remote, or current workspace.

---

## Step 1: Determine Repository Source

Analyze the user's input to determine source type:

| Input Pattern | Source Type | Action |
|---------------|-------------|--------|
| `https://github.com/...` | GitHub Remote | Clone via git |
| `https://gitlab.com/...` | GitLab Remote | Clone via git |
| `https://bitbucket.org/...` | Bitbucket Remote | Clone via git |
| `git@...` | SSH Remote | Clone via git |
| `/path/to/dir` or `./relative` | Local Path | Validate & use |
| `org/repo` (short form) | GitHub Short | Expand to full URL, clone |
| (no input / "this repo") | Current Workspace | Use CWD |

---

## Step 2: Execute Acquisition

### For Remote Repositories:

```bash
# Clone to temporary analysis directory
git clone --depth=1 {REPO_URL} /tmp/dep-analysis-{REPO_NAME}

# If specific branch requested:
git clone --depth=1 --branch {BRANCH} {REPO_URL} /tmp/dep-analysis-{REPO_NAME}

# Capture metadata
cd /tmp/dep-analysis-{REPO_NAME}
git log -1 --format="%H %ai %s"
git rev-parse --abbrev-ref HEAD
find . -type f | wc -l
du -sh .
```

### For Local Paths:

```bash
# Validate directory exists
test -d {PATH} && echo "Valid" || echo "Not found"

# Check if git repo
git -C {PATH} rev-parse --is-inside-work-tree

# Capture metadata
cd {PATH}
git log -1 --format="%H %ai %s"
git rev-parse --abbrev-ref HEAD
find . -type f -not -path './.git/*' | wc -l
```

### For Current Workspace:

```bash
# Use current directory
pwd
git log -1 --format="%H %ai %s" 2>/dev/null || echo "Not a git repo"
git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "N/A"
```

---

## Step 3: Detect Monorepo Structure

Check for monorepo indicators:

```bash
# Check for common monorepo tools
test -f lerna.json && echo "MONOREPO: Lerna"
test -f nx.json && echo "MONOREPO: Nx"
test -f turbo.json && echo "MONOREPO: Turborepo"
test -f pnpm-workspace.yaml && echo "MONOREPO: pnpm workspaces"

# Check package.json for workspaces
grep -l '"workspaces"' package.json 2>/dev/null && echo "MONOREPO: npm/yarn workspaces"

# Check for Maven multi-module
grep -l '<modules>' pom.xml 2>/dev/null && echo "MONOREPO: Maven multi-module"

# Check for Gradle multi-project
grep -l 'include' settings.gradle* 2>/dev/null && echo "MONOREPO: Gradle multi-project"
```

---

## Step 4: Generate Repository Metadata

Produce a structured summary:

```markdown
## Repository Metadata
- **Name**: {repo_name}
- **Source**: {local|github|gitlab|bitbucket|current}
- **URL**: {url_or_path}
- **Branch**: {branch}
- **Last Commit**: {hash} ({date})
- **File Count**: {count}
- **Repository Size**: {size}
- **Monorepo**: {yes/no} ({tool if yes})
- **Workspace Packages**: {list if monorepo}
```

---

## Step 5: Validation Checks

Before proceeding, validate:

- [ ] Repository is accessible (permissions OK)
- [ ] Repository contains source code (not empty)
- [ ] At least one recognizable project file exists
- [ ] Git history is available (for change analysis)

If validation fails, report the issue and ask user for guidance.

---

## Completion Message

```
✅ Repository Acquired
   Name: {repo_name}
   Branch: {branch} (last commit: {date})
   Size: {file_count} files, {size}
   Monorepo: {yes/no}

   Proceeding to Tech Stack Detection...
```

---

## Error Handling

| Error | Resolution |
|-------|-----------|
| Clone failed (auth) | Ask user for credentials or suggest SSH key |
| Clone failed (not found) | Verify URL, check visibility (public/private) |
| Empty repository | Report and ask if user wants to proceed |
| Path not found | Ask user to verify the path |
| Not a git repo | Warn but proceed (limited metadata available) |
