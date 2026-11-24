#!/usr/bin/env bash
# Remove everything the service stack created. Optionally set IMAGE_REPO to also delete the ECR
# repository you created for the image.
#
# Order matters. Protected tasks block scale-in, and an ACCEPTING task re-protects itself on its next
# renewal, so a release alone can be undone before the stack is deleted:
#   1. Set the desired count to 0. Every protection renewal then fails with DEPLOYMENT_BLOCKED.
#   2. Release protection on every task.
#   3. Verify with get-task-protection, and release again if a renewal got in first.
#   4. Wait until DescribeTasks reports every task STOPPED (runningCount drops too early to trust).
#   5. Delete the stack.
set -euo pipefail
STACK=${STACK:-voice-ecs-service}
RELEASE_ATTEMPTS=${RELEASE_ATTEMPTS:-10}
STOP_WAIT_SECONDS=${STOP_WAIT_SECONDS:-1500}   # deregistration delay (default 900 s) + stopTimeout

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

# Both desired statuses: once the count is 0, ECS marks tasks desiredStatus STOPPED while they are still
# running (and draining), so listing only RUNNING could miss a task that is still protected.
running_tasks() {
  local status
  for status in RUNNING STOPPED; do
    aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" --desired-status "$status" \
      --query 'taskArns[]' --output text
  done | tr '\t\n' '  '
}

# update-task-protection takes up to 10 tasks per call. A task that already stopped is reported under
# failures (TASK_NOT_VALID); that is printed, not fatal.
release_all() {
  local arns=("$@") i
  for ((i = 0; i < ${#arns[@]}; i += 10)); do
    aws ecs update-task-protection --cluster "$CLUSTER" --tasks "${arns[@]:i:10}" \
      --no-protection-enabled --query 'failures[].[arn,reason]' --output text
  done
}

# get-task-protection takes up to 100 tasks per call. Prints the ARNs that are still protected.
still_protected() {
  local arns=("$@") i
  for ((i = 0; i < ${#arns[@]}; i += 100)); do
    aws ecs get-task-protection --cluster "$CLUSTER" --tasks "${arns[@]:i:100}" \
      --query 'protectedTasks[?protectionEnabled].taskArn' --output text
  done
}

# describe-tasks takes up to 100 tasks per call. Prints "ARN lastStatus" for every task that has not
# reached lastStatus STOPPED. A task ECS no longer knows (MISSING) has stopped and is not printed.
not_stopped() {
  local arns=("$@") i
  for ((i = 0; i < ${#arns[@]}; i += 100)); do
    aws ecs describe-tasks --cluster "$CLUSTER" --tasks "${arns[@]:i:100}" \
      --query "tasks[?lastStatus!='STOPPED'].[taskArn,lastStatus]" --output text
  done
}

if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  CLUSTER=$(out ClusterName)
  # Record this stack's task definitions now: once the stack is gone, its resource list can't tell which
  # revisions it created. Only these are deleted at the end (never other task definitions in the account).
  TASK_DEFS=$(aws cloudformation list-stack-resources --stack-name "$STACK" \
    --query "StackResourceSummaries[?ResourceType=='AWS::ECS::TaskDefinition'].PhysicalResourceId" --output text)
  SERVICE=$(aws cloudformation describe-stack-resource --stack-name "$STACK" --logical-resource-id CallingService \
    --query 'StackResourceDetail.PhysicalResourceId' --output text)
  SERVICE=${SERVICE##*/}

  echo "1/5 setting the desired count of $SERVICE to 0 (renewals now fail with DEPLOYMENT_BLOCKED)"
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0 --query 'service.desiredCount' \
    --output text >/dev/null

  verified=false
  for ((attempt = 1; attempt <= RELEASE_ATTEMPTS; attempt++)); do
    read -r -a tasks <<< "$(running_tasks)"
    if [ "${#tasks[@]}" -eq 0 ]; then
      verified=true
      break
    fi
    echo "2/5 releasing protection on ${#tasks[@]} task(s), attempt $attempt"
    release_all "${tasks[@]}"
    read -r -a protected <<< "$(still_protected "${tasks[@]}")"
    if [ "${#protected[@]}" -eq 0 ]; then
      echo "3/5 verified with get-task-protection: no task is protected"
      verified=true
      break
    fi
    echo "3/5 still protected: ${protected[*]}"
    sleep 10
  done
  if [ "$verified" != true ]; then
    echo "tasks are still protected after $RELEASE_ATTEMPTS attempts; not deleting the stack" >&2
    exit 1
  fi

  deadline=$((SECONDS + STOP_WAIT_SECONDS))
  while :; do
    # runningCount drops to 0 as soon as ECS starts stopping a task, while it can still drain for the
    # whole deregistration delay, so ask DescribeTasks for each task's lastStatus instead.
    read -r -a tasks <<< "$(running_tasks)"
    pending=""
    if [ "${#tasks[@]}" -gt 0 ]; then
      pending=$(not_stopped "${tasks[@]}")
    fi
    if [ -z "$pending" ]; then
      echo "4/5 running tasks: 0 (DescribeTasks reports every task in $SERVICE as STOPPED)"
      break
    fi
    summary=$(printf '%s\n' "$pending" | awk '{print $2}' | sort | uniq -c | awk '{printf "%s%s %s", sep, $1, $2; sep=", "}')
    echo "4/5 waiting for tasks to reach lastStatus STOPPED: $summary"
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "tasks did not stop within ${STOP_WAIT_SECONDS}s; not deleting the stack" >&2
      exit 1
    fi
    sleep 15
  done

  echo "5/5 deleting stack $STACK"
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK"
fi
if [ -n "${IMAGE_REPO:-}" ]; then
  aws ecr delete-repository --repository-name "$IMAGE_REPO" --force >/dev/null
fi
# CloudFormation deregisters the stack's task definitions (they become INACTIVE) but doesn't delete them.
# Delete exactly the revisions this stack created, recorded before the stack was deleted.
for td in ${TASK_DEFS:-}; do
  status=$(aws ecs describe-task-definition --task-definition "$td" --query 'taskDefinition.status' --output text)
  if [ "$status" = "ACTIVE" ]; then
    aws ecs deregister-task-definition --task-definition "$td" --query 'taskDefinition.status' --output text >/dev/null
  fi
  aws ecs delete-task-definitions --task-definitions "$td" --query 'failures[].[arn,reason]' --output text
  echo "deleted task definition ${td##*/} (was $status)"
done
# Container Insights creates this log group outside the stack (untagged), so CloudFormation leaves it behind.
if [ -n "${CLUSTER:-}" ]; then
  CI_LOG_GROUP="/aws/ecs/containerinsights/$CLUSTER/performance"
  if [ "$(aws logs describe-log-groups --log-group-name-prefix "$CI_LOG_GROUP" \
        --query "length(logGroups[?logGroupName=='$CI_LOG_GROUP'])" --output text)" = "1" ]; then
    aws logs delete-log-group --log-group-name "$CI_LOG_GROUP"
    echo "deleted log group $CI_LOG_GROUP"
  fi
fi
echo "Resources still tagged Article=Voice-ECS (the tagging API lists deleted resources for a while; see the README):"
aws resourcegroupstaggingapi get-resources --tag-filters Key=Article,Values=Voice-ECS --query 'ResourceTagMappingList[].ResourceARN' --output text
