#!/usr/bin/env bash
# Run one controller command as a Fargate task in the service VPC, wait for it, and print its log.
# Usage: ./run_controller.sh scale-in | scale-out | abort | status | python -c "..."
set -euo pipefail
STACK=${STACK:-voice-ecs-service}
out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
CLUSTER=$(out ClusterName); TD=$(out ControllerTaskDef); SG=$(out ControllerSg)
SUBNETS=$(out PrivateSubnetIds); LG=$(out LogGroup)
if [ "$1" = "python" ]; then CMD=$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1:]))' "$@")
else CMD=$(python3 -c 'import json,sys;print(json.dumps(["python","controller.py"]+sys.argv[1:]))' "$@"); fi
ARN=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TD" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"controller\",\"command\":$CMD}]}" \
  --propagate-tags TASK_DEFINITION --tags key=Article,value=Voice-ECS --query 'tasks[0].taskArn' --output text)
echo "controller task $ARN" >&2
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$ARN" || aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$ARN"
aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$ARN" --query 'tasks[0].containers[0].{exit:exitCode,reason:reason}' --output json >&2
aws logs get-log-events --log-group-name "$LG" --log-stream-name "controller/controller/${ARN##*/}" --start-from-head --query 'events[].message' --output text | tr '\t' '\n'
