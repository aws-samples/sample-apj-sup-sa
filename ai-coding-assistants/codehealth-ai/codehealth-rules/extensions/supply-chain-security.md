# Supply Chain Security Extension - Full Rules

## Enforcement Rules

When this extension is enabled, the following checks are MANDATORY during the
Security Vulnerability Scan stage:

### Rule 1: Typosquatting Detection
- Compare all package names against top-1000 packages in the ecosystem
- Flag any package with Levenshtein distance <= 2 from a popular package
- Flag recently published packages (< 30 days) with similar names

### Rule 2: Maintainer Trust Assessment
- Flag packages where maintainer changed in last 90 days
- Flag packages with single maintainer and high dependency count
- Check if maintainer email domain is still active

### Rule 3: Install Script Analysis
- For Node.js: Check for `preinstall`, `postinstall`, `install` scripts
- Flag scripts that execute network calls
- Flag scripts that access filesystem outside package directory
- Flag minified/obfuscated install scripts

### Rule 4: Provenance Verification
- Check for npm provenance (SLSA Build L3)
- Verify package was built from declared source repository
- Flag packages without any provenance attestation (warning only)

### Rule 5: Registry Source Verification
- Verify all packages come from expected registries
- Flag packages from non-standard registries without .npmrc/.pip.conf config
- Check for dependency confusion vectors (internal package names on public registry)

## Non-Compliance is BLOCKING
Any finding from these rules must be acknowledged by the user before proceeding.
