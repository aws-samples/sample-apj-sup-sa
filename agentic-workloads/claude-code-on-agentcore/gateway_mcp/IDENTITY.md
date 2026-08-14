# Why GitHub App + Secrets Manager instead of AgentCore Identity

This sample authenticates to GitHub using a **GitHub App installation token**,
minted server-side in the gateway (`app/main.py`) and stored only in memory,
with the App's private key pulled from **AWS Secrets Manager**. It does not
use **AgentCore Identity** for the GitHub credential. Rationale:

- **Least privilege, no static PAT.** A GitHub App installation token is
  scoped to the exact repositories/permissions granted at installation time
  (Contents/Issues/PRs — no admin, no delete), and expires in about an hour.
  A personal access token would be broader and longer-lived by default.
- **No token ever leaves this account.** The private key lives in Secrets
  Manager; the gateway's IAM execution role is the only principal that can
  read it (`secretsmanager:GetSecretValue` scoped to the specific secret
  ARN). AgentCore Identity is designed for delegating *user* identity and
  OAuth token vending across agent invocations — this sample has no user
  identity to delegate; it's a single service-to-service GitHub integration,
  so a directly-held (Secrets Manager-backed) credential is simpler and
  keeps the trust boundary in one place we already control.
- **Minimal moving parts for a sample.** Secrets Manager + IAM is a pattern
  most readers already know. Introducing AgentCore Identity's OAuth/token
  vault flow here would add setup steps (identity provider config, token
  vault, consent flow) without a corresponding benefit, since there's no
  end-user identity in this flow to protect.

If you're adapting this sample to a multi-user product where each end user
needs their own delegated GitHub access, AgentCore Identity's token vault is
the right tool — this sample's single-installation-token approach assumes
one shared GitHub App installation for the whole gateway.
