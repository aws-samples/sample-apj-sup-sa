# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please open a private security advisory
or contact the maintainers directly. Do not file public issues for security reports.

## Secrets & Configuration

This project never commits secrets. All credentials are loaded from environment
variables at runtime. Before running any component:

1. Copy the relevant `.env.example` to `.env` and fill in your own values.
2. Never commit `.env`, `.agentcore_identity_*`, or any file containing API keys,
   tokens, AWS account IDs, or passwords.
3. Generate a strong `JWT_SECRET`:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

The following files are intentionally gitignored and must remain local:

- `.env`, `apps/customer-app/.env`
- `.agentcore_identity_cognito_m2m.json`
- `.agentcore_identity_m2m.env`
- `customer-tools-openapi-with-server.json`
- `*.pem`, `*.key`

## Production Hardening Notes

The demo defaults are intended for local development. Before deploying to production:

- Set `DEMO_MODE = False` in `services/backend/app/auth.py` so OTP codes are never
  returned in API responses.
- Replace the in-memory OTP store with a persistent, TTL-backed store (Redis/DynamoDB).
- Scope IAM execution roles to least privilege.
- Restrict CORS origins instead of using wildcards.
