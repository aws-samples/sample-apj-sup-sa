#!/usr/bin/env bash
# Provision the OpenSearch domain used for call transcript search and turn-level logs.
#
# INSTANCE TYPE: r6g.large.search, and deliberately NOT the cheaper t3.small.
#
# This domain started on t3.small.search (~$26/mo) as the smallest sensible demo
# footprint. That was wrong for this workload and it failed in a way worth
# recording, because the symptoms pointed away from the cause: the classifier
# suites began returning 504 Gateway Time-out from the domain's front-end proxy
# while CloudWatch showed the cluster essentially IDLE - CPU 29%, JVM heap 33%,
# SearchLatency 0.67ms, zero ThreadpoolSearchRejected, cluster status green.
#
# That combination is the signature of a burstable instance exhausting its CPU
# credits. The hypervisor caps the vCPU, so the node cannot answer the proxy
# inside its timeout; utilisation reads LOW precisely because the node is not
# permitted to do work, and nothing shows up as a rejection because requests
# never reach a queue to be rejected from. Reading those metrics at face value
# would have sent us looking for a query problem that did not exist.
#
# Three independent reasons the t3 was the wrong choice:
#
#   1. Burstable CPU sits on the live call path. Every conversational turn runs a
#      kNN search, and barge-in gives the whole turn a budget in the hundreds of
#      milliseconds. A node that can stall for seconds when credits run out is
#      disqualified by its worst case, not judged by its average.
#   2. k-NN holds the HNSW graph OFF-heap. t3.small has 2 GiB total, so ~1 GiB
#      JVM heap and ~1 GiB shared between the OS and that graph. r6g.large has
#      16 GiB. The concept index is the long-tail interpreter, so it is precisely
#      the index expected to grow.
#   3. One node serves two competing workloads: hvi-icd-concepts (latency-critical
#      reads, on the call path) and hvi-call-transcripts (a write every turn).
#
# Memory-optimised r6g is the documented family for k-NN for reason 2. Chose it
# over m6g.large (8 GiB, ~$102/mo) because the extra headroom removes off-heap
# memory as a variable entirely, and over t3.medium because that is still
# burstable and so does not address reason 1 at all.
#
# Reason 3 is only mitigated here, not solved: 16 GiB is enough that transcript
# writes and clinical reads no longer contend meaningfully at demo volume. In
# production these belong on separate domains, so that log ingestion can never
# degrade classification on a live call.
#
# Cost: roughly $0.186/hr (~$134/mo) plus storage, against ~$26/mo before.
#
# The domain uses a public endpoint guarded by an IAM access policy rather than
# being placed in a VPC. That keeps the non-VPC Lambdas able to reach it with
# plain SigV4 signing and avoids paying for interface endpoints or a NAT gateway.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

DOMAIN="${PREFIX}-logs"
INSTANCE_TYPE="${OPENSEARCH_INSTANCE_TYPE:-r6g.large.search}"
ACC="$AWS_ACCOUNT_ID"

if aws opensearch describe-domain --domain-name "$DOMAIN" --region "$AWS_REGION" >/dev/null 2>&1; then
  hvi_warn "OpenSearch domain $DOMAIN already exists"
  aws opensearch describe-domain --domain-name "$DOMAIN" --region "$AWS_REGION" \
    --query 'DomainStatus.{Endpoint:Endpoint,Processing:Processing,Created:Created}' --output json
  exit 0
fi

# Pick the newest available OpenSearch engine version.
ENGINE=$(aws opensearch list-versions --region "$AWS_REGION" \
  --query "Versions[?starts_with(@,'OpenSearch_')]|[0]" --output text)
hvi_log "using engine version $ENGINE"

# Least-privilege-ish access policy: only our two roles and the deploying admin.
# The CreateDomain API rejects newlines in accessPolicies, so this is compacted
# to a single line with jq before being passed.
ACCESS_POLICY=$(jq -c . <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": [
          "arn:aws:iam::${ACC}:role/${PREFIX}-lambda-role",
          "arn:aws:iam::${ACC}:role/${PREFIX}-agentcore-role",
          "arn:aws:iam::${ACC}:user/adminCLI"
        ]
      },
      "Action": "es:ESHttp*",
      "Resource": "arn:aws:es:${AWS_REGION}:${ACC}:domain/${DOMAIN}/*"
    }
  ]
}
JSON
)

hvi_log "creating OpenSearch domain $DOMAIN ($INSTANCE_TYPE)"
aws opensearch create-domain \
  --domain-name "$DOMAIN" \
  --engine-version "$ENGINE" \
  --cluster-config "InstanceType=${INSTANCE_TYPE},InstanceCount=1,DedicatedMasterEnabled=false,ZoneAwarenessEnabled=false" \
  --ebs-options "EBSEnabled=true,VolumeType=gp3,VolumeSize=10" \
  --node-to-node-encryption-options "Enabled=true" \
  --encryption-at-rest-options "Enabled=true" \
  --domain-endpoint-options "EnforceHTTPS=true,TLSSecurityPolicy=Policy-Min-TLS-1-2-2019-07" \
  --access-policies "$ACCESS_POLICY" \
  --region "$AWS_REGION" \
  --tag-list Key=Project,Value=health-voice-intake \
  --query 'DomainStatus.{Domain:DomainName,Created:Created}' --output json

hvi_record "opensearch.domain" "$DOMAIN"

hvi_log "OpenSearch is provisioning in the background (typically 15-25 minutes)."
hvi_log "Check status with: bash scripts/status.sh"
