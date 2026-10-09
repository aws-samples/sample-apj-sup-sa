# IAM Temporary Delegation — Reference (post-launch support access)

Out of scope for the container build itself — this is an **operating-the-listing** capability for
after the model is published. Included here as a pointer for sellers who ask "how do I support a
customer's endpoint without a long-lived cross-account role."

Source: AWS IAM User Guide — [IAM temporary delegation](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies-temporary-delegation.html),
and the AWS ML blog ["Deepgram enhances Amazon SageMaker AI support with AWS IAM Temporary
Delegation"](https://aws.amazon.com/blogs/machine-learning/deepgram-enhances-amazon-sagemaker-ai-support-with-aws-iam-temporary-delegation/)
(2026-07-27), which documents a reference integration for exactly this seller use case.

## The problem it solves

A buyer's SageMaker endpoint misbehaves (bad output, stuck loading, 5XX spike). The seller's support
team needs enough account access to investigate — but standing cross-account IAM roles or shared
credentials are a compliance liability the buyer's security team will push back on, and per-incident
screen-shares don't scale.

## How it works

1. The buyer logs into the seller's product/support flow and starts an integration or support action.
2. The seller (product provider) **initiates a delegation request** naming the specific AWS services
   and actions needed, and redirects the buyer to the AWS Management Console.
3. The buyer reviews the exact, fully-resolved permissions (no wildcards) and approves, denies, or
   forwards the request to their own administrator.
4. Once approved, the seller obtains **short-lived STS credentials** scoped to the approved
   permissions — up to **12 hours** maximum. (Root-user approvers are capped at 4 hours; longer
   durations require a non-root approver.)
5. Access expires automatically. No manual cleanup, no standing cross-account role left behind —
   unless the request explicitly provisioned one for ongoing operations (see below).
6. Every delegated action is tagged and logged in the buyer's **AWS CloudTrail** for audit.

```
Buyer's product/support console → delegation request → Buyer approves in IAM console
        → seller receives scoped STS creds (≤12h) → CloudTrail-logged activity → auto-expiry
```

## Ongoing access (optional)

If the seller needs persistent access (e.g., continuously reading a CloudWatch log group rather than
one-off investigation), the delegation request can include **creating an IAM role** that survives
past the temporary window. That role **must** carry a permission boundary — a cap on its maximum
permissions that the buyer reviews and approves as part of the same request. The boundary limits
blast radius even if the role's attached policy is later widened.

## Relevant IAM permissions (buyer side)

| Permission | What it does |
|---|---|
| `iam:AssociateDelegationRequest` | Associate an unassigned request with the account |
| `iam:GetDelegationRequest` | View request details |
| `iam:UpdateDelegationRequest` | Forward a request to an administrator |
| `iam:AcceptDelegationRequest` | Approve a request |
| `iam:SendDelegationToken` | Release the exchange token to the provider after approval |
| `iam:RejectDelegationRequest` | Reject a request |
| `iam:ListDelegationRequests` | List requests for the account |

Administrators can grant these selectively to delegate approval authority to specific teams rather
than requiring root/admin for every approval.

## Constraints that matter for a seller evaluating this

- **Only qualified AWS Partners and Amazon products can initiate requests.** This requires completing
  a separate AWS Partner onboarding process for the temporary-delegation feature — it is not something
  a seller's container or `CreateModelPackage` call gets for free. Buyers can only *approve*, never
  initiate.
- **12-hour hard cap** per delegation window. Not a substitute for a persistent support role if the
  investigation genuinely spans days — plan for re-requesting or the IAM-role-with-boundary path above.
- **Buyer must already hold the permissions being delegated.** If the buyer's own account lacks a
  requested permission, the provider does not receive it even after approval — the check fails silently
  from the provider's side, not the buyer's.
- This replaces the older pattern of asking buyers to create a long-lived cross-account IAM role
  trusting the seller's account ID + external ID. If a seller is currently asking Marketplace buyers to
  do that, temporary delegation is the direction AWS is pushing partners toward instead.

## Where this fits in the SageMaker Marketplace onboarding flow

Not a Phase in this skill's container walkthrough — it has no effect on `/ping`, `/invocations`,
weights packaging, or `CreateModelPackage`. Surface it only when a seller who has already published a
listing (Phase 11 complete) asks about **customer support access** post-launch. Point them at:

- Partner Integration Guide: https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies-temporary-delegation-partner-guide.html
- AWS Partner Central, to start the qualification process for initiating delegation requests.

This skill does not implement the integration — it is account/product-level AWS Partner enablement,
not container code.
