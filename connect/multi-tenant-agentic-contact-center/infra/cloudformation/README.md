# AnyCompanyPay — CloudFormation deployment

A standalone CloudFormation template ([`anycompany-pay.yaml`](anycompany-pay.yaml)) that provisions the same
architecture as the CDK app: VPC, internal ALB, ECS Fargate (CPU autoscaling), a CloudFront
distribution using a **VPC origin**, and Cognito (groups + users) for client-side auth.

This is an **alternative** to the CDK app in `infra/`. Don't run both against the same account/region
with the same names — the Cognito domain prefix in particular must be globally unique (use a
different `CognitoDomainPrefix`).

## Why two steps

CloudFormation can't build a container image (CDK's asset pipeline does that). So you:

1. **Build & push** the SPA image to ECR.
2. **Deploy** the template, passing the image URI.

## 1. Build & push the image

```bash
# from the repo root — region/account come from your AWS session; set it once:
export AWS_REGION=<your-region>
./infra/cloudformation/build-and-push.sh "$AWS_REGION" anycompany-pay-web latest
```

The script creates the ECR repo if needed, builds `linux/amd64`, pushes, and prints the
`IMAGE_URI` plus a ready-to-run deploy command.

## 2. Deploy the stack

```bash
aws cloudformation deploy \
  --template-file infra/cloudformation/anycompany-pay.yaml \
  --stack-name anycompany-pay \
  --capabilities CAPABILITY_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides \
      ContainerImageUri=<IMAGE_URI from step 1> \
      CognitoDomainPrefix=anycompany-pay-app-<something-unique>
```

Optional parameters (with defaults):

| Parameter | Default | Notes |
|---|---|---|
| `ContainerImageUri` | — (required) | ECR image URI from step 1 |
| `CognitoDomainPrefix` | `anycompany-pay-app` | Must be globally unique in the region |
| `AdminEmail` | `admin@anycompany-pay.example` | Admin user (group `admin`) |
| `MerchantEmail` | `merchant@anycompany-pay.example` | Merchant user (group `merchant`) |
| `TaskCpu` / `TaskMemory` | `256` / `512` | Fargate task size |
| `MinTasks` / `MaxTasks` | `1` / `4` | Autoscaling bounds |

### Demo user passwords

There is **no password parameter**. The template creates one
`AWS::SecretsManager::Secret` per demo user with `GenerateSecretString`, and a helper Lambda
custom resource reads the secret by ARN to set a permanent Cognito password (the
`AWS::Cognito::UserPoolUser` resource can't set one directly). No plaintext password exists in
the template, in stack parameters, in stack events, or in the custom resource's properties.

Retrieve a login after deploying — the secret names are stack outputs
(`AdminPasswordSecret`, `MerchantPasswordSecret`):

```bash
aws secretsmanager get-secret-value \
  --secret-id <stack-name>/cognito/admin \
  --query SecretString --output text
```

Rotate through Secrets Manager for anything beyond a demo.

## 3. Get the outputs

```bash
aws cloudformation describe-stacks --stack-name anycompany-pay --region "$AWS_REGION" \
  --query "Stacks[0].Outputs" --output table
```

Open the `CloudFrontUrl`. The landing page is public; choosing a workspace prompts login. Sign in
with the admin or merchant user — each can only enter its own workspace.

> First deploy takes ~15 min (CloudFront distribution + VPC origin propagation). The ALB target
> becomes healthy a minute or two after the service starts.

## Updating the app

Rebuild/push a new image (step 1), then redeploy with the new `ContainerImageUri`. If the tag is
unchanged (`:latest`), force a new task by updating any task-def field or push a unique tag.

## Deleting

```bash
aws cloudformation delete-stack --stack-name anycompany-pay --region "$AWS_REGION"
```

The ECR repository is **not** part of the stack; delete it separately if desired:
`aws ecr delete-repository --repository-name anycompany-pay-web --force --region "$AWS_REGION"`.

## What's in the template

- **Networking:** VPC (2 AZs), public subnets + IGW, private subnets + 1 NAT gateway.
- **Security:** ALB SG allows inbound only from the CloudFront origin-facing managed prefix list
  (looked up by a helper Lambda custom resource); service SG allows only the ALB.
- **Compute:** ECS Fargate service (nginx serving the SPA), CPU target-tracking autoscaling (min 1).
- **Edge:** CloudFront distribution → `AWS::CloudFront::VpcOrigin` → internal ALB.
- **Auth:** Cognito user pool, hosted-UI domain, `admin`/`merchant` groups, a public SPA client
  (Authorization Code + PKCE), and two users placed in their groups. Cognito config is injected
  into the ECS task as env vars; the container writes `/auth-config.json` for the SPA to read.

## Differences vs the CDK app

- The CDK app builds & pushes the image automatically; here it's a separate script.
- The CDK app keeps an idle Lambda@Edge function (historical). This template omits it entirely —
  auth is fully client-side, so it was never needed here.
