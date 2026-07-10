# Contributing to TastyVoice

Thanks for your interest in contributing. This guide covers how to set up the
project, propose changes, and submit pull requests.

## Getting Started

1. Fork the repository and clone your fork.
2. Follow the [Quickstart in the README](README.md#quickstart-local) to run the
   services locally.
3. Copy each service's `.env.example` to `.env` and fill in your own values.
   Never commit secrets.

## Project Layout

- `apps/` — frontend applications (React + Vite)
- `services/` — backend services (FastAPI, AgentCore, Lambda)
- `docs/` — architecture, demo guide, and API specs

## Making Changes

1. Create a feature branch:
   ```bash
   git checkout -b feature/short-description
   ```
2. Keep changes focused and scoped to a single concern.
3. Match the existing code style and conventions of the service you're editing.
4. Update documentation when you change behavior or configuration.

## Commit Messages

Write clear, imperative commit messages (e.g., "Add dietary filter to menu API").
Reference related issues where applicable.

## Pull Requests

- Describe what changed and why.
- Note how you tested the change.
- Ensure no secrets, credentials, or personal data are included in the diff.
- Keep PRs reasonably small to make review easier.

## Reporting Bugs & Requesting Features

Open an issue with a clear title, reproduction steps (for bugs), and the expected
versus actual behavior. For security issues, follow [SECURITY.md](SECURITY.md)
instead of opening a public issue.

## Code of Conduct

By participating, you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).
