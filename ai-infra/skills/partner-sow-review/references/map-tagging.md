# Reference: MAP (Migration Acceleration Program) Requirements

**Official docs:** https://docs.aws.amazon.com/MAP/latest/userguide/what-is-service.html
**Tagging guide:** https://s3.us-west-2.amazonaws.com/map-2.0-customer-documentation/html/AWSMapDocs/getting-started.html

## MAP tagging — Critical for funding

MAP credits are only applied to resources tagged with the `map-migrated` tag.
**Untagged resources do not receive credits.** This is the #1 operational
mistake in MAP engagements.

### Requirements

| Requirement | Detail |
|---|---|
| Tag key | `map-migrated` |
| Tag value | Migration ID (provided by AWS, e.g., `d-server-xxxxxxxxxxxx`) |
| Applied to | All migrated/modernized resources |
| Auto-activated | Yes — as a cost allocation tag (doesn't count toward quota) |
| Credits applied | Only to resources with this tag |

### SoW review checklist for MAP deals

| Check | Status | Notes |
|---|---|---|
| Does the SoW mention MAP tagging? | Required | Must be explicit in the implementation plan |
| Is tagging implemented via IaC? | Required | CloudFormation/CDK/Terraform — not manual |
| Is there a tag compliance mechanism? | Recommended | AWS Config rule `required-tags` or similar |
| Is there a process for untagged resources? | Recommended | Weekly audit + remediation plan |
| Is AWS MGN (Migration Hub) used? | Preferred | Automatically applies MAP tags to migrated servers |
| Is there a tag audit in the SoW timeline? | Recommended | Monthly tag compliance review during engagement |

### Red flags for MAP SoWs

| Issue | Risk | Impact |
|---|---|---|
| No mention of `map-migrated` tag | Credits won't be applied to any resources | $0 credit utilization |
| Manual tagging plan (no IaC) | Tags will be forgotten on new resources | Ongoing credit leakage |
| No tag compliance monitoring | Untagged resources accumulate over time | Significant credit loss |
| Tag applied after deployment (not in template) | New resources created by auto-scaling/CI won't be tagged | Intermittent credit gaps |
| Multiple migration IDs confused | Wrong tag value = credits attributed to wrong project | Accounting issues |

### How MAP tagging should appear in IaC

**CloudFormation:**
```yaml
Resources:
  MyInstance:
    Type: AWS::EC2::Instance
    Properties:
      Tags:
        - Key: map-migrated
          Value: d-server-xxxxxxxxxxxx
```

**CDK (TypeScript):**
```typescript
import { Tags } from 'aws-cdk-lib';
Tags.of(this).add('map-migrated', 'd-server-xxxxxxxxxxxx');
```

**Terraform:**
```hcl
resource "aws_instance" "example" {
  tags = {
    "map-migrated" = "d-server-xxxxxxxxxxxx"
  }
}

# Or use default_tags in provider block for all resources:
provider "aws" {
  default_tags {
    tags = {
      "map-migrated" = "d-server-xxxxxxxxxxxx"
    }
  }
}
```

### Tag compliance automation

The SoW should include one of:

1. **AWS Config rule** — `required-tags` rule checking for `map-migrated`
2. **Service Control Policy (SCP)** — Deny resource creation without the tag
3. **CI/CD gate** — Template validation rejects deployments missing the tag
4. **AWS Tag Policies** — Enforce tag syntax and required tags org-wide

### MAP-specific funding validation

| Question | Good answer | Bad answer |
|---|---|---|
| How will credits be tracked? | Cost Explorer filtered by `map-migrated` tag | "We'll check monthly" |
| What happens to untagged resources? | Auto-remediation or weekly audit with bulk tagging | "We'll tag them later" |
| Is the migration ID known? | Yes, provided by AWS PSA/PDM | "We don't have it yet" (can't start without it) |
| Are auto-scaled resources tagged? | Yes, launch templates include the tag | Not considered |
| Are CI/CD-deployed resources tagged? | Yes, IaC templates include the tag | Manual post-deployment tagging |

## Integration with SoW review

When reviewing a MAP-funded SoW:

1. **Search for "map-migrated"** or "MAP tag" in the document
2. If not found → **Critical flag** (credits will not apply)
3. If found, verify it's in IaC (not manual tagging plan)
4. Check for compliance mechanism (Config rule, SCP, or CI/CD gate)
5. Verify the migration ID is referenced (or noted as "to be provided by AWS")
6. Ensure auto-scaling and CI/CD paths include the tag
