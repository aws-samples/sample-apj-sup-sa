# Required IAM permissions

## To deploy the CloudFormation stack

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudFormation",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStackResources",
        "cloudformation:GetTemplate"
      ],
      "Resource": "arn:aws:cloudformation:*:*:stack/bedrock-fm-dashboard/*"
    },
    {
      "Sid": "Dashboard",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutDashboard",
        "cloudwatch:DeleteDashboards",
        "cloudwatch:GetDashboard",
        "cloudwatch:ListDashboards"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Alarms",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricAlarm",
        "cloudwatch:DeleteAlarms",
        "cloudwatch:DescribeAlarms"
      ],
      "Resource": "arn:aws:cloudwatch:*:*:alarm:Bedrock-FM-Dashboard-*"
    },
    {
      "Sid": "LogsQueryDefinitions",
      "Effect": "Allow",
      "Action": [
        "logs:PutQueryDefinition",
        "logs:DeleteQueryDefinition",
        "logs:DescribeQueryDefinitions"
      ],
      "Resource": "*"
    }
  ]
}
```

## To enable Model Invocation Logging (one-time setup)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockLoggingConfig",
      "Effect": "Allow",
      "Action": [
        "bedrock:GetModelInvocationLoggingConfiguration",
        "bedrock:PutModelInvocationLoggingConfiguration"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CreateLogGroup",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:PutRetentionPolicy"
      ],
      "Resource": "arn:aws:logs:*:*:log-group:/aws/bedrock/*"
    },
    {
      "Sid": "CreateLoggingRole",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:PutRolePolicy",
        "iam:GetRole",
        "iam:PassRole"
      ],
      "Resource": "arn:aws:iam::*:role/bedrock-modelinvocation-logging-*"
    }
  ]
}
```

## What the dashboard *reads* at runtime

The dashboard itself is just a JSON body — viewers need:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetDashboard",
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics",
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:DescribeLogGroups"
      ],
      "Resource": "*"
    }
  ]
}
```
