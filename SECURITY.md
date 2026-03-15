# Security Policy

## Reporting a Vulnerability

Please do not open a public issue for a suspected security vulnerability.

Use GitHub's private vulnerability reporting if it is enabled for the repository. If private reporting is not available, contact the maintainer directly rather than opening a public issue — even for low-risk hardening suggestions.

## Scope

Security-sensitive areas include:

- accidental exposure of cookies or exported browser sessions
- command-injection or path-handling bugs
- unsafe handling of local media and subtitle paths
- secret leakage in docs, assets, examples, or tests

## Repository Hygiene

The repository CI checks both tracked files and git history for obvious leaks:

- tracked-file hygiene blocks personal email leakage and local state artifacts
- `gitleaks` scans repository history for hardcoded secrets before changes are merged

If you discover a real leaked credential, rotate it first and avoid posting the secret itself in issues or reports.

## Expectations for Reports

- include reproduction steps
- include the affected command and platform
- avoid posting secrets or private media
- describe impact clearly
