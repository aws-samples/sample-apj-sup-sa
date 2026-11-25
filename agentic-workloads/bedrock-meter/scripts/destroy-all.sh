#!/usr/bin/env bash
# One command, one choice. AWS CLI + jq only; no CDK build, Python, or saved state.
set -euo pipefail

case "${1:-}" in
  --help|-h)
    echo "Usage: scripts/destroy-all.sh"
    echo "Choose once: keep stored data, or permanently remove this installation and its data."
    echo "Requires AWS CLI v2 and jq. Uses the terminal's AWS credentials/region."
    exit 0 ;;
esac
if [ "$#" -ne 0 ]; then
  echo "error: usage: scripts/destroy-all.sh (no non-interactive deletion flags)" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_PAGER="" AWS_CLI_AUTO_PROMPT=off
# shellcheck source=_aws-context.sh
. "$ROOT/scripts/_aws-context.sh"
# Do not load project options: uninstall uses the live stack and terminal AWS target.
resolve_aws_target
STACK=BedrockMeterCoreStack
WORK="$(mktemp -d "${TMPDIR:-/tmp}/bedrock-meter-uninstall.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
STACK_ID=""
STACK_DELETED=false

report_failure() {
  echo "Uninstall incomplete. Resolve the reported error before any further cleanup." >&2
  [ -z "$STACK_ID" ] || echo "Stack ID: $STACK_ID" >&2
  if [ -f "$WORK/remaining.json" ]; then
    echo "Stored resources not confirmed removed:" >&2
    jq -r '.[] | "  \(.ResourceType): \(.PhysicalResourceId)"' "$WORK/remaining.json" >&2
  fi
  if [ "$STACK_DELETED" = true ]; then
    echo "The stack is already deleted. Use the resource list above for manual cleanup." >&2
  fi
}
trap 'status=$?; report_failure; exit "$status"' ERR

fail() { echo "error: $*" >&2; report_failure; exit 1; }
aws_read() {
  AWS_CLI_ERROR_FORMAT=legacy aws "$@" --region "$REGION" --output json --no-cli-pager \
    2>"$WORK/aws-error"
}
show_aws_error() { cat "$WORK/aws-error" >&2; }

if ! aws_read cloudformation describe-stacks --stack-name "$STACK" >"$WORK/stack.json"; then
  if grep -Fq '(ValidationError)' "$WORK/aws-error" &&
     grep -Fq "Stack with id $STACK does not exist" "$WORK/aws-error"; then
    echo "No $STACK in this AWS account/region. No changes made."
    echo "Previously retained resources are not automatically discovered or deleted."
    exit 0
  fi
  show_aws_error
  fail "Cannot read the stack."
fi
STACK_ID="$(jq -er '.Stacks[0].StackId' "$WORK/stack.json")"
PARTITION="$(printf '%s' "$STACK_ID" | cut -d: -f2)"
[[ "$STACK_ID" == "arn:$PARTITION:cloudformation:$REGION:$ACCOUNT:stack/$STACK/"* ]] ||
  fail "Stack ARN does not match the current AWS target."
STATUS="$(jq -er '.Stacks[0].StackStatus' "$WORK/stack.json")"
case "$STATUS" in
  *_IN_PROGRESS) fail "Stack is $STATUS. Wait for that operation to finish." ;;
esac

aws_read cloudformation get-template --stack-name "$STACK_ID" --template-stage Original \
  >"$WORK/template-response.json" || { show_aws_error; fail "Cannot read the deployed template."; }
jq -e '.TemplateBody | if type == "string" then fromjson else . end' \
  "$WORK/template-response.json" >"$WORK/template.json"
aws_read cloudformation list-stack-resources --stack-name "$STACK_ID" \
  >"$WORK/resources.json" || { show_aws_error; fail "Cannot list stack resources."; }
# Invocation logging is a Bedrock account/region facility, not Meter data to purge.
# Protect both the destinations declared by this stack and its currently configured destinations.
aws_read bedrock get-model-invocation-logging-configuration >"$WORK/logging.json" ||
  { show_aws_error; fail "Cannot identify Bedrock logging resources to preserve."; }
jq --slurpfile r "$WORK/resources.json" --slurpfile live "$WORK/logging.json" '
  . as $t |
  ($r[0].StackResourceSummaries | map({key: .LogicalResourceId, value: .PhysicalResourceId}) | from_entries) as $ids |
  ([.Resources[] | .Properties.LoggingConfig? // empty] + [$live[0].loggingConfig // {}]
   | [.. | strings]) as $targets |
  [.Resources | to_entries[] | . as $r |
   select(
     ((.value.Type == "AWS::S3::Bucket" or .value.Type == "AWS::Logs::LogGroup" or .value.Type == "AWS::IAM::Role")
      and ((.key | test("^BedrockLogging(InvocationLogGroup|LogArchiveBucket|BedrockLoggingRole)"))
           or ($targets | index($r.key) != null)
           or ($ids[$r.key] != null and ($targets | index($ids[$r.key]) != null))
           or (.value.Type == "AWS::IAM::Role" and $ids[$r.key] != null
               and any($targets[]; contains(":iam::") and contains(":role/") and endswith("/" + $ids[$r.key])))))
     or (.value.Type == "AWS::CloudFormation::CustomResource" and .value.Properties.LoggingConfig != null)
     or (.value.Type == "Custom::AWS" and (.key | startswith("BedrockLoggingPutLoggingConfig")))
   ) | .key] as $base |
  ($base | map($ids[.]) | map(select(. != null))) as $names |
  $base + [.Resources | to_entries[] |
    select(
      (.value.Type == "AWS::IAM::Policy" and any(.value.Properties.Roles[]?;
        if type == "object" then .Ref as $ref | $base | index($ref) != null
        else . as $name | $names | index($name) != null end))
      or (.value.Type == "AWS::S3::BucketPolicy" and (.value.Properties.Bucket |
        if type == "object" then .Ref as $ref | $base | index($ref) != null
        else . as $name | $names | index($name) != null end))
    ) | .key] | unique
' "$WORK/template.json" >"$WORK/logging-ids.json"
jq --slurpfile t "$WORK/template.json" --slurpfile protected "$WORK/logging-ids.json" '
  [.StackResourceSummaries[] | select(.PhysicalResourceId != null)
   | .LogicalResourceId as $id | select($protected[0] | index($id) == null)
   | select($t[0].Resources[.LogicalResourceId].DeletionPolicy
            | . == "Retain" or . == "RetainExceptOnCreate")]
' "$WORK/resources.json" >"$WORK/remaining.json"

echo ""
echo "Stack:    $STACK"
echo "Stack ID: $STACK_ID"
echo "Meter data resources:"
jq -r '.[] | "  \(.ResourceType): \(.PhysicalResourceId)"' "$WORK/remaining.json"
cat <<'PROMPT'

How would you like to uninstall Bedrock Meter?

1. Remove the service, keep stored data
   Preserve Meter usage, settings, audit records, and console users.

2. Remove the service and Meter data
   Permanently delete the listed data resources.

Both options preserve Bedrock invocation logging, its logs, storage, and delivery permissions.
Only Meter service resources and its quota-deny policies are removed.
IAM principals outside this stack and shared CDK bootstrap resources are excluded.
Backups and exported copies outside the listed resources are not deleted.
PROMPT
CHOICE=""
printf 'Select [1/2, Enter to cancel]: '
read -r CHOICE || true
case "$CHOICE" in
  1|2) ;;
  *) echo "Cancelled. No AWS changes made."; exit 0 ;;
esac

# Stable identities protect against a same-name resource being replaced while deletion runs.
identity() {
  local row="$1" type name stack_tag
  type="$(jq -r '.ResourceType' <<<"$row")"
  name="$(jq -r '.PhysicalResourceId' <<<"$row")"
  case "$type" in
    AWS::DynamoDB::Table)
      aws_read dynamodb describe-table --table-name "$name" >"$WORK/identity.json" ||
        { show_aws_error; return 1; }
      jq -er '.Table.TableId' "$WORK/identity.json" ;;
    AWS::IAM::Role)
      aws_read iam get-role --role-name "$name" >"$WORK/identity.json" ||
        { show_aws_error; return 1; }
      jq -er '.Role.RoleId' "$WORK/identity.json" ;;
    AWS::S3::Bucket)
      if aws_read s3api get-bucket-tagging --bucket "$name" --expected-bucket-owner "$ACCOUNT" >"$WORK/identity.json"; then
        stack_tag="$(jq -r '[.TagSet[] | select(.Key == "aws:cloudformation:stack-id") | .Value][0] // empty' "$WORK/identity.json")"
      elif grep -q '(NoSuchTagSet)' "$WORK/aws-error"; then
        stack_tag=""
      else
        show_aws_error; return 1
      fi
      if [ "$stack_tag" = "$STACK_ID" ]; then
        printf '%s\n' "$STACK_ID"
      elif [ -z "$stack_tag" ] && [ "$STACK_DELETED" = true ] &&
           [ "$(jq -r '.identity // empty' <<<"$row")" = "$STACK_ID" ]; then
        # Stack deletion can remove tags from retained buckets. Only accept a
        # bucket verified before deletion, with its account ownership rechecked.
        aws_read s3api head-bucket --bucket "$name" --expected-bucket-owner "$ACCOUNT" >/dev/null ||
          { show_aws_error; return 1; }
        printf '%s\n' "$STACK_ID"
      else
        echo "error: Cannot verify stack ownership of bucket: $name" >&2
        return 1
      fi ;;
    AWS::Cognito::UserPool)
      aws_read cognito-idp describe-user-pool --user-pool-id "$name" >"$WORK/identity.json" ||
        { show_aws_error; return 1; }
      jq -er '.UserPool.Arn' "$WORK/identity.json" ;;
    AWS::Logs::LogGroup)
      aws_read logs describe-log-groups --log-group-name-prefix "$name" >"$WORK/identity.json" ||
        { show_aws_error; return 1; }
      jq -er --arg name "$name" '.logGroups[] | select(.logGroupName == $name) | .creationTime' "$WORK/identity.json" ;;
    *) echo "error: Unsupported retained resource: $type $name" >&2; return 1 ;;
  esac
}
USAGE_TABLE="$(jq -er '.Stacks[0].Outputs[] | select(.OutputKey == "UsageTableName") | .OutputValue' "$WORK/stack.json")"
USAGE_ROW="$(jq -ec --arg name "$USAGE_TABLE" '.[] | select(.ResourceType == "AWS::DynamoDB::Table" and .PhysicalResourceId == $name)' "$WORK/remaining.json")"
USAGE_ID="$(identity "$USAGE_ROW")" || fail "Cannot verify the usage table."
if [ "$CHOICE" = 2 ]; then
  while IFS= read -r row; do
    id="$(identity "$row")" || fail "Cannot verify ownership/identity of a stored resource."
    jq -c --arg id "$id" '. + {identity: $id}' <<<"$row" >>"$WORK/data.ndjson"
  done < <(jq -c '.[]' "$WORK/remaining.json")
  jq -s . "$WORK/data.ndjson" >"$WORK/data.json"
else
  cp "$WORK/remaining.json" "$WORK/data.json"
fi

# Preserve the delivery policies too: retaining just a log bucket/role would leave
# Bedrock logging broken when CloudFormation deletes its bucket/inline IAM policies.
# New deployments already have these policies; update older stacks without rebuilding CDK.
jq -c --slurpfile ids "$WORK/logging-ids.json" '
  reduce $ids[0][] as $id (.;
    .Resources[$id].DeletionPolicy = "Retain" |
    .Resources[$id].UpdateReplacePolicy = "Retain")
' "$WORK/template.json" >"$WORK/retain-logging.json"
if ! jq -e --slurpfile ids "$WORK/logging-ids.json" '
  . as $t | all($ids[0][];
    $t.Resources[.].DeletionPolicy == "Retain" and $t.Resources[.].UpdateReplacePolicy == "Retain")
' "$WORK/template.json" >/dev/null; then
  jq '[.Stacks[0].Parameters[]? | {ParameterKey,UsePreviousValue:true}]' "$WORK/stack.json" >"$WORK/parameters.json"
  jq '[.Stacks[0].Tags[]? | select(.Key | startswith("aws:") | not)]' "$WORK/stack.json" >"$WORK/tags.json"
  STAGE_BUCKET=""
  STAGE_KEY=""
  STAGE_VERSION=""
  TEMPLATE_ARGS=(--template-body "file://$WORK/retain-logging.json")
  if [ "$(wc -c < "$WORK/retain-logging.json")" -gt 51200 ]; then
    # Use a verified Meter-owned query-results bucket, never a logging/external bucket.
    STAGE_ROW="$(jq -ec '[.[] | select(.ResourceType == "AWS::S3::Bucket")][0] // empty' "$WORK/remaining.json")" ||
      fail "No Meter-owned bucket is available to stage the retention update. Apply the updated CDK logging retention policies before uninstalling."
    identity "$STAGE_ROW" >/dev/null || fail "Cannot verify the template staging bucket."
    STAGE_BUCKET="$(jq -r '.PhysicalResourceId' <<<"$STAGE_ROW")"
    STAGE_KEY="bedrock-meter-uninstall/${WORK##*/}.json"
    aws_read s3api put-object --bucket "$STAGE_BUCKET" --key "$STAGE_KEY" \
      --expected-bucket-owner "$ACCOUNT" --if-none-match '*' --body "$WORK/retain-logging.json" \
      >"$WORK/staged.json" || { show_aws_error; fail "Could not stage the retention update."; }
    STAGE_VERSION="$(jq -r '.VersionId // empty' "$WORK/staged.json")"
    DNS_SUFFIX=amazonaws.com
    [ "$PARTITION" != aws-cn ] || DNS_SUFFIX=amazonaws.com.cn
    TEMPLATE_ARGS=(--template-url "https://s3.$REGION.$DNS_SUFFIX/$STAGE_BUCKET/$STAGE_KEY")
  fi
  UPDATE_FAILED=0
  if aws_read cloudformation update-stack --stack-name "$STACK_ID" "${TEMPLATE_ARGS[@]}" \
       --parameters "file://$WORK/parameters.json" --tags "file://$WORK/tags.json" \
       --capabilities CAPABILITY_NAMED_IAM >"$WORK/update.json"; then
    aws_read cloudformation wait stack-update-complete --stack-name "$STACK_ID" >/dev/null ||
      { show_aws_error; UPDATE_FAILED=1; }
  elif ! grep -Fq 'No updates are to be performed' "$WORK/aws-error"; then
    show_aws_error
    UPDATE_FAILED=1
  fi
  if [ -n "$STAGE_BUCKET" ]; then
    DELETE_ARGS=(s3api delete-object --bucket "$STAGE_BUCKET" --key "$STAGE_KEY"
                 --expected-bucket-owner "$ACCOUNT")
    [ -z "$STAGE_VERSION" ] || DELETE_ARGS+=(--version-id "$STAGE_VERSION")
    aws_read "${DELETE_ARGS[@]}" >/dev/null ||
      { show_aws_error; fail "Could not remove staged template $STAGE_BUCKET/$STAGE_KEY. Stack deletion was not started."; }
  fi
  [ "$UPDATE_FAILED" = 0 ] || fail "Could not preserve logging permissions. Stack deletion was not started."
  aws_read cloudformation get-template --stack-name "$STACK_ID" --template-stage Original \
    >"$WORK/updated-template.json" || { show_aws_error; fail "Cannot confirm logging retention."; }
  jq -e --slurpfile ids "$WORK/logging-ids.json" '
    (.TemplateBody | if type == "string" then fromjson else . end) as $t |
    all($ids[0][]; $t.Resources[.].DeletionPolicy == "Retain" and $t.Resources[.].UpdateReplacePolicy == "Retain")
  ' "$WORK/updated-template.json" >/dev/null || fail "Logging resources are not retained. Stack deletion was not started."
fi
aws_read cloudformation delete-stack --stack-name "$STACK_ID" >/dev/null ||
  { show_aws_error; fail "Stack deletion failed."; }
echo "Waiting for stack deletion..."
aws_read cloudformation wait stack-delete-complete --stack-name "$STACK_ID" >/dev/null ||
  { show_aws_error; fail "Stack deletion did not complete."; }
STACK_DELETED=true

# Deleted Lambda functions can still have requests in flight. Let quota writers finish
# before removing their policies, otherwise they could immediately reattach a deny.
GRACE="$(jq -er '[.Resources[] | select(.Type == "AWS::Lambda::Function")
  | select((.Properties.Environment.Variables // {}) | has("USAGE_TABLE"))
  | (.Properties.Timeout // 3)] | (max // 0) + 5' "$WORK/template.json")"
[[ "$GRACE" =~ ^[0-9]+$ ]] || fail "Cannot determine the metering shutdown wait."
echo "Waiting up to ${GRACE}s for in-flight metering requests to finish..."
for ((left=GRACE; left>0; left-=10)); do
  if [ "$left" -gt 10 ]; then sleep 10; else sleep "$left"; fi
done
[ "$(identity "$USAGE_ROW")" = "$USAGE_ID" ] || fail "Usage table changed during uninstall."
aws_read dynamodb scan --table-name "$USAGE_TABLE" --projection-expression UserARN --consistent-read \
  >"$WORK/principals.json" || { show_aws_error; fail "Cannot identify quota-deny policies."; }
jq -r '[.Items[]?.UserARN.S | select(type == "string")] | unique[]' "$WORK/principals.json" >"$WORK/principals"
cat >"$WORK/deny.json" <<'POLICY'
{"Version":"2012-10-17","Statement":[{"Sid":"AutoBlockBedrockQuotaExceeded","Effect":"Deny","Action":["bedrock:Invoke*","bedrock:Converse*","bedrock:RetrieveAndGenerate*","bedrock:InvokeAgent*"],"Resource":"*"}]}
POLICY
NORMALIZE='.Statement |= (if type == "object" then [.] else . end) | .Statement |= map(.Action |= (if type == "array" then sort else . end))'
jq -S "$NORMALIZE" "$WORK/deny.json" >"$WORK/expected-policy.json"
FAILED=0
while IFS= read -r arn; do
  case "$arn" in
    "arn:$PARTITION:iam::$ACCOUNT:user/"*) kind=user ;;
    "arn:$PARTITION:iam::$ACCOUNT:role/"*) kind=role ;;
    *) continue ;;
  esac
  name="${arn##*/}"
  if aws_read iam "get-$kind-policy" "--$kind-name" "$name" \
       --policy-name AutoBlocked-BedrockMeter-QuotaExceeded >"$WORK/policy.json"; then
    if [ "$(jq -r '.PolicyDocument | type' "$WORK/policy.json")" = string ]; then
      encoded="$(jq -r '.PolicyDocument' "$WORK/policy.json")"
      printf '%b' "${encoded//%/\\x}" >"$WORK/policy-document.json"
    else
      jq '.PolicyDocument' "$WORK/policy.json" >"$WORK/policy-document.json"
    fi
    if ! jq -S "$NORMALIZE" "$WORK/policy-document.json" >"$WORK/actual-policy.json" ||
       ! cmp -s "$WORK/expected-policy.json" "$WORK/actual-policy.json"; then
      echo "error: Quota policy was changed; left untouched: $arn" >&2
      FAILED=1
      continue
    fi
    if ! aws_read iam "delete-$kind-policy" "--$kind-name" "$name" \
           --policy-name AutoBlocked-BedrockMeter-QuotaExceeded >/dev/null; then
      show_aws_error
      echo "error: Quota policy remains on $arn" >&2
      FAILED=1
    fi
  elif grep -Fq '(NoSuchEntity)' "$WORK/aws-error"; then
    continue
  else
    show_aws_error
    echo "error: Could not inspect quota policy on $arn" >&2
    FAILED=1
  fi
done <"$WORK/principals"
[ "$FAILED" = 0 ] || fail "Quota-policy cleanup incomplete; stored data has not been purged."

empty_bucket() {
  local bucket="$1" upload
  while :; do
    aws_read s3api list-multipart-uploads --bucket "$bucket" --expected-bucket-owner "$ACCOUNT" \
      --max-uploads 1000 --no-paginate >"$WORK/uploads.json" || { show_aws_error; return 1; }
    [ "$(jq '.Uploads // [] | length' "$WORK/uploads.json")" -gt 0 ] || break
    while IFS= read -r upload; do
      jq --arg b "$bucket" --arg a "$ACCOUNT" \
        '{Bucket:$b,ExpectedBucketOwner:$a,Key:.Key,UploadId:.UploadId}' <<<"$upload" >"$WORK/abort.json"
      aws_read s3api abort-multipart-upload --cli-input-json "file://$WORK/abort.json" >/dev/null ||
        { show_aws_error; return 1; }
    done < <(jq -c '.Uploads[]' "$WORK/uploads.json")
  done
  while :; do
    aws_read s3api list-object-versions --bucket "$bucket" --expected-bucket-owner "$ACCOUNT" \
      --max-keys 1000 --no-paginate >"$WORK/versions.json" || { show_aws_error; return 1; }
    jq '{Objects: [(.Versions // [])[], (.DeleteMarkers // [])[]]
         | .[:1000] | map({Key,VersionId} | with_entries(select(.value != null))), Quiet:true}' \
      "$WORK/versions.json" >"$WORK/delete.json"
    [ "$(jq '.Objects | length' "$WORK/delete.json")" -gt 0 ] || break
    aws_read s3api delete-objects --bucket "$bucket" --expected-bucket-owner "$ACCOUNT" \
      --delete "file://$WORK/delete.json" >"$WORK/deleted.json" || { show_aws_error; return 1; }
    if ! jq -e '(.Errors // []) | length == 0' "$WORK/deleted.json" >/dev/null; then
      jq '.Errors' "$WORK/deleted.json" >&2
      return 1
    fi
  done
  aws_read s3api delete-bucket --bucket "$bucket" --expected-bucket-owner "$ACCOUNT" >/dev/null ||
    { show_aws_error; return 1; }
}

purge() {
  local row="$1" type name current
  type="$(jq -r '.ResourceType' <<<"$row")"
  name="$(jq -r '.PhysicalResourceId' <<<"$row")"
  current="$(identity "$row")" || return 1
  [ "$current" = "$(jq -r '.identity' <<<"$row")" ] ||
    { echo "error: Resource identity changed: $name" >&2; return 1; }
  case "$type" in
    AWS::S3::Bucket) empty_bucket "$name" || return 1 ;;
    AWS::DynamoDB::Table)
      aws_read dynamodb delete-table --table-name "$name" >/dev/null || { show_aws_error; return 1; }
      aws_read dynamodb wait table-not-exists --table-name "$name" >/dev/null || { show_aws_error; return 1; } ;;
    AWS::Logs::LogGroup)
      aws_read logs delete-log-group --log-group-name "$name" >/dev/null || { show_aws_error; return 1; } ;;
    AWS::Cognito::UserPool)
      aws_read cognito-idp delete-user-pool --user-pool-id "$name" >/dev/null || { show_aws_error; return 1; } ;;
    AWS::IAM::Role)
      # Unexpected policies/instance profiles cause DeleteConflict; never detach them to force deletion.
      aws_read iam delete-role --role-name "$name" >/dev/null || { show_aws_error; return 1; } ;;
  esac
}

if [ "$CHOICE" = 1 ]; then
  echo "Service removed. Stored resources preserved:"
  jq -r '.[] | "  \(.ResourceType): \(.PhysicalResourceId)"' "$WORK/remaining.json"
  echo "A fresh deployment does not automatically reuse these retained resources."
  exit 0
fi
FAILED=0
while IFS= read -r row; do
  name="$(jq -r '.PhysicalResourceId' <<<"$row")"
  if purge "$row"; then
    echo "Deleted: $name"
    logical="$(jq -r '.LogicalResourceId' <<<"$row")"
    jq --arg id "$logical" 'map(select(.LogicalResourceId != $id))' "$WORK/remaining.json" >"$WORK/next.json"
    mv "$WORK/next.json" "$WORK/remaining.json"
  else
    echo "error: Not fully removed: $name" >&2
    FAILED=1
  fi
done < <(jq -c '.[]' "$WORK/data.json")
[ "$FAILED" = 0 ] || fail "Some stored resources could not be removed."
echo "Meter stack and all listed Meter data resources deleted. Bedrock invocation logging and its logs are preserved."
echo "IAM principals outside the stack, shared bootstrap resources, and separate backups/exports were not deleted."
