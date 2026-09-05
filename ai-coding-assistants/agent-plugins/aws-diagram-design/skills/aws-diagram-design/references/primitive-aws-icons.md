# AWS Architecture Icons (primitive)

The official AWS Architecture Icons, release **07312026**, bundled with this skill under [`assets/aws-icons/`](../assets/aws-icons/). Use these — not the generic monochrome `aws` silhouette — whenever a diagram names specific AWS services (EC2, Lambda, S3, Bedrock, …). Browse visually in [`assets/aws-icons.html`](../assets/aws-icons.html); look up any file in [`assets/aws-icons/INDEX.md`](../assets/aws-icons/INDEX.md).

**812 icons**: 305 service, 466 resource, 15 group, 26 category.

## When to use which set

| Set | Path | Canvas (`viewBox`) | Anatomy | Use for |
|---|---|---|---|---|
| **Service** | `service/<Category>/Arch_<Service>_48.svg` | `0 0 64 64` | Solid category-color square + white glyph | A named AWS service as a node |
| **Resource** | `resource/<Category>/Res_<Service>_<Resource>_48.svg` | `0 0 48 48` | Category-color glyph, transparent bg | A resource *within* a service (EC2 instance, Lambda function, S3 object) and generic marks (`General-Icons`: Users, Documents, Internet, …) |
| **Group** | `group/<Name>_32.svg` | `0 0 40 40` | Solid color square + white glyph | Corner badge on a zone/container border |
| **Category** | `category/Arch-Category_<Name>_48.svg` | `0 0 74 74` | Category-color square + white glyph | A whole category, service not yet chosen |

Mixing is normal: service icons for nodes, group badges on containers, resource icons for interior detail. If the diagram is about generic infrastructure (any cloud, no named AWS service), stay with the monochrome set in [primitive-icons.md](primitive-icons.md) instead.

## Category colors (extracted from this release — ground truth)

| Hex | Categories |
|---|---|
| `#ED7100` orange | Compute, Containers, Media Services, Blockchain, Quantum |
| `#8C4FFF` purple | Analytics, Networking & Content Delivery, Games |
| `#C925D1` magenta | Databases, Developer Tools, Customer Enablement, Satellite |
| `#DD344C` red | Security & Identity, Front-End Web & Mobile, Business Applications |
| `#7AA116` green | Storage, IoT, Cloud Financial Management |
| `#01A88D` teal | Artificial Intelligence, Migration & Modernization, End User Computing |
| `#E7157B` pink | Application Integration, Management & Governance |
| `#232F3E` squid ink | General icons, AWS Cloud |
| `#00A4A6` dark teal | Region, subnets (group set only) |
| `#7D8998` grey | Corporate data center, generic server (group set only) |

## Lookup workflow

1. Check the common-services table below — it covers most diagrams.
2. Not there → search `assets/aws-icons/INDEX.md` for the service name.
3. Still unsure → glob the directory: `ls <skill-dir>/assets/aws-icons/service/*/`. Naming follows the official package: `Arch_Amazon-EC2_48.svg`, `Res_Amazon-EC2_Instance_48.svg`, General resource icons end in `_48_Light.svg`.

Renamed services to know: SageMaker → `Arch_Amazon-SageMaker-AI`, QuickSight → `Arch_Amazon-Quick` (Quick Suite), plain ECS/EKS → `Arch_Amazon-Elastic-Container-Service` / `Arch_Amazon-Elastic-Kubernetes-Service`.

### Common services (relative to `assets/aws-icons/`)

| Service | Path |
|---|---|
| EC2 | `service/Compute/Arch_Amazon-EC2_48.svg` |
| Lambda | `service/Compute/Arch_AWS-Lambda_48.svg` |
| Elastic Beanstalk | `service/Compute/Arch_AWS-Elastic-Beanstalk_48.svg` |
| App Runner | `service/Compute/Arch_AWS-App-Runner_48.svg` |
| Batch | `service/Compute/Arch_AWS-Batch_48.svg` |
| ECS | `service/Containers/Arch_Amazon-Elastic-Container-Service_48.svg` |
| EKS | `service/Containers/Arch_Amazon-Elastic-Kubernetes-Service_48.svg` |
| Fargate | `service/Containers/Arch_AWS-Fargate_48.svg` |
| ECR | `service/Containers/Arch_Amazon-Elastic-Container-Registry_48.svg` |
| S3 | `service/Storage/Arch_Amazon-Simple-Storage-Service_48.svg` |
| S3 Glacier | `service/Storage/Arch_Amazon-Simple-Storage-Service-Glacier_48.svg` |
| EFS | `service/Storage/Arch_Amazon-EFS_48.svg` |
| AWS Backup | `service/Storage/Arch_AWS-Backup_48.svg` |
| RDS | `service/Databases/Arch_Amazon-RDS_48.svg` |
| Aurora | `service/Databases/Arch_Amazon-Aurora_48.svg` |
| DynamoDB | `service/Databases/Arch_Amazon-DynamoDB_48.svg` |
| ElastiCache | `service/Databases/Arch_Amazon-ElastiCache_48.svg` |
| DMS | `service/Databases/Arch_AWS-Database-Migration-Service_48.svg` |
| API Gateway | `service/Networking-Content-Delivery/Arch_Amazon-API-Gateway_48.svg` |
| CloudFront | `service/Networking-Content-Delivery/Arch_Amazon-CloudFront_48.svg` |
| Route 53 | `service/Networking-Content-Delivery/Arch_Amazon-Route-53_48.svg` |
| ELB | `service/Networking-Content-Delivery/Arch_Elastic-Load-Balancing_48.svg` |
| VPC | `service/Networking-Content-Delivery/Arch_Amazon-Virtual-Private-Cloud_48.svg` |
| Transit Gateway | `service/Networking-Content-Delivery/Arch_AWS-Transit-Gateway_48.svg` |
| Direct Connect | `service/Networking-Content-Delivery/Arch_AWS-Direct-Connect_48.svg` |
| PrivateLink | `service/Networking-Content-Delivery/Arch_AWS-PrivateLink_48.svg` |
| SQS | `service/Application-Integration/Arch_Amazon-Simple-Queue-Service_48.svg` |
| SNS | `service/Application-Integration/Arch_Amazon-Simple-Notification-Service_48.svg` |
| EventBridge | `service/Application-Integration/Arch_Amazon-EventBridge_48.svg` |
| Step Functions | `service/Application-Integration/Arch_AWS-Step-Functions_48.svg` |
| Bedrock | `service/Artificial-Intelligence/Arch_Amazon-Bedrock_48.svg` |
| SageMaker AI | `service/Artificial-Intelligence/Arch_Amazon-SageMaker-AI_48.svg` |
| Amazon Q | `service/Artificial-Intelligence/Arch_Amazon-Q_48.svg` |
| Kinesis | `service/Analytics/Arch_Amazon-Kinesis_48.svg` |
| MSK | `service/Analytics/Arch_Amazon-Managed-Streaming-for-Apache-Kafka_48.svg` |
| Glue | `service/Analytics/Arch_AWS-Glue_48.svg` |
| Athena | `service/Analytics/Arch_Amazon-Athena_48.svg` |
| Redshift | `service/Analytics/Arch_Amazon-Redshift_48.svg` |
| EMR | `service/Analytics/Arch_Amazon-EMR_48.svg` |
| OpenSearch | `service/Analytics/Arch_Amazon-OpenSearch-Service_48.svg` |
| CloudWatch | `service/Management-Tools/Arch_Amazon-CloudWatch_48.svg` |
| CloudTrail | `service/Management-Tools/Arch_AWS-CloudTrail_48.svg` |
| Systems Manager | `service/Management-Tools/Arch_AWS-Systems-Manager_48.svg` |
| Organizations | `service/Management-Tools/Arch_AWS-Organizations_48.svg` |
| IAM | `service/Security-Identity/Arch_AWS-Identity-and-Access-Management_48.svg` |
| KMS | `service/Security-Identity/Arch_AWS-Key-Management-Service_48.svg` |
| Cognito | `service/Security-Identity/Arch_Amazon-Cognito_48.svg` |
| Secrets Manager | `service/Security-Identity/Arch_AWS-Secrets-Manager_48.svg` |
| WAF | `service/Security-Identity/Arch_AWS-WAF_48.svg` |
| Shield | `service/Security-Identity/Arch_AWS-Shield_48.svg` |
| GuardDuty | `service/Security-Identity/Arch_Amazon-GuardDuty_48.svg` |
| Users (generic) | `resource/General-Icons/Res_Users_48_Light.svg` |
| User (generic) | `resource/General-Icons/Res_User_48_Light.svg` |
| Internet (generic) | `resource/General-Icons/Res_Internet_48_Light.svg` |
| EC2 instance | `resource/Compute/Res_Amazon-EC2_Instance_48.svg` |
| Lambda function | `resource/Compute/Res_AWS-Lambda_Lambda-Function_48.svg` |

## Inlining an icon

Read the SVG file, keep only its **inner `<g>`** (drop the XML declaration, the outer `<svg>` wrapper, and the `<title>`), strip `id` attributes (two icons from the same category collide on the BG-group id), and wrap in a positioned group:

```svg
<!-- 24×24 service icon at (x,y): scale = 24 / 64 = 0.375 -->
<g transform="translate(X,Y) scale(0.375)" aria-hidden="true">
  <g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
    <g fill="#ED7100"><rect x="0" y="0" width="64" height="64"></rect></g>
    <path d="…glyph…" fill="#FFFFFF"></path>
  </g>
</g>
```

Scale factors for a 24px render: service `24/64 = 0.375`, resource `24/48 = 0.5`, group badge `24/40 = 0.6`, category `24/74 ≈ 0.324` (use 32px → `32/74` if the odd ratio fights the 4px grid). Round the *placement* to the 4px grid; the internal icon geometry is exempt (like stroke widths).

### Node pattern with a service icon

The icon replaces the rectangular type tag from SKILL.md §6 — don't stack both:

```svg
<rect x="X" y="Y" width="176" height="56" rx="6" fill="#ffffff"/>          <!-- opaque mask -->
<rect x="X" y="Y" width="176" height="56" rx="6" fill="#ffffff" stroke="#232F3E" stroke-width="1"/>
<g transform="translate(X+12, Y+16) scale(0.375)" aria-hidden="true">      <!-- 24px icon, left -->
  …inlined icon group…
</g>
<text x="X+48" y="Y+26" fill="#232F3E" font-size="12" font-weight="600"
      font-family="'Amazon Ember', 'Helvetica Neue', Arial, sans-serif">Order API</text>
<text x="X+48" y="Y+42" fill="#545B64" font-size="9"
      font-family="'Amazon Ember Mono', ui-monospace, monospace">Lambda · python3.13</text>
```

### Compact nodes (non-architecture types)

**The icon rule applies to every visual type, not just Architecture.** Data-flow cells, process steps, high-level components, DP-integration nodes, sequence actors, swimlane steps, medallion tiers — any element whose subject is a named AWS service carries the official icon.

Types whose nodes are too small for the icon-left pattern (e.g., the 100×64 data-flow cell, which already spends its left corner on a role chip) use the **16px corner slot** instead:

```svg
<!-- 16×16 service icon in the node's top-right corner: scale = 16 / 64 = 0.25 -->
<g transform="translate(NODE_X + NODE_W - 20, NODE_Y + 4) scale(0.25)" aria-hidden="true">
  …inlined icon group (ids stripped)…
</g>
```

Rules for the corner slot:

- **16px is the floor** (rule 2 below) — never render smaller to squeeze a tight cell; drop the icon from secondary nodes instead.
- Scale factors at 16px: service `16/64 = 0.25`, resource `16/48 ≈ 0.333`, group badge `16/40 = 0.4`.
- **One icon per node** — the node's primary service. A node listing two tools (`Kinesis · DMS`) shows the dominant one; the sublabel already names the rest.
- **Keep the title clear of the icon.** With a centered title and a 16px top-right icon on a 100px-wide node, cap the title at ~13 characters; if the title can't shrink, move to the icon-left pattern or a wider node.
- Nodes for non-AWS subjects (roles, generic actors, on-prem tools) take no AWS icon — mixing generic monochrome marks ([primitive-icons.md](primitive-icons.md)) in the same diagram is fine.

### Group containers (zones)

AWS zone containers follow the official convention: **border in the group color, badge in the top-left corner, label to the badge's right**. Fill is `none` or a ≤5% tint of the border color.

| Zone | Border | Style | Badge file |
|---|---|---|---|
| AWS Cloud | `#242F3E` (light) / `#232F3E` (dark) | solid | `group/AWS-Cloud_32.svg` (`_Dark` variant for dark skin) |
| AWS account | `#E7157B` | solid | `group/AWS-Account_32.svg` |
| Region | `#00A4A6` | dashed | `group/Region_32.svg` |
| Availability Zone | `#00A4A6` | dashed | *none — text label only* |
| VPC | `#8C4FFF` | solid | `group/Virtual-private-cloud-VPC_32.svg` |
| Private subnet | `#00A4A6` | solid | `group/Private-subnet_32.svg` |
| Public subnet | `#7AA116` | solid | `group/Public-subnet_32.svg` |
| Security group | `#DD344C` | dashed | *none — text label only* |
| Auto Scaling group | `#ED7100` | dashed | `group/Auto-Scaling-group_32.svg` (badge top-center is also official) |
| Corporate data center | `#7D8998` | solid | `group/Corporate-data-center_32.svg` |
| EC2 instance contents | `#ED7100` | solid | `group/EC2-instance-contents_32.svg` |
| Spot Fleet | `#ED7100` | solid | `group/Spot-Fleet_32.svg` |
| Server contents | `#7D8998` | solid | `group/Server-contents_32.svg` |

```svg
<!-- VPC container -->
<rect x="X" y="Y" width="W" height="H" fill="none" stroke="#8C4FFF" stroke-width="1"/>
<g transform="translate(X,Y) scale(0.6)" aria-hidden="true">…group/Virtual-private-cloud-VPC_32.svg inner g…</g>
<text x="X+32" y="Y+16" fill="#232F3E" font-size="12" font-weight="600"
      font-family="'Amazon Ember', 'Helvetica Neue', Arial, sans-serif">VPC · 10.0.0.0/16</text>
```

Zone dashes use `stroke-dasharray="4,4"`; nested zones inset by ≥16px. These zone borders are *identity colors* and don't count against the accent budget — but the §6 connector rules and §7 complexity budget still apply unchanged.

## Rules (official brand + skill integration)

1. **Never recolor, restyle, distort, rotate, or crop an official icon.** If the diagram must be monochrome, use the generic set ([primitive-icons.md](primitive-icons.md)) instead of tinting AWS icons.
2. **Minimum render 16px**; 24–32px inside nodes is the sweet spot. Keep the 1:1 aspect ratio.
3. **Icon and zone-border colors are identity marks, exempt from the accent budget.** The `accent` (#FF9900) budget of 1–2 still governs editorial elements: focal node strokes, highlighted arrows, callouts.
4. **Don't pair a full-color icon with a colored type tag** — the icon already carries the category signal. Node text stays `ink`/`muted`.
5. **Latest names**: this release renames several services (see Lookup workflow). Label nodes with the current service name, not the icon's historical one.
6. **Density**: full-color icons are visually loud. Above ~7 icons, consider zoning the diagram or dropping icons from secondary nodes (text-only nodes are fine).

## License attribution

AWS Architecture Icons © Amazon Web Services, Inc. — provided by AWS for use in architecture diagrams per the AWS Architecture Icons terms (https://aws.amazon.com/architecture/icons/). Do not alter the icons or use them to imply AWS sponsorship of non-AWS products.
