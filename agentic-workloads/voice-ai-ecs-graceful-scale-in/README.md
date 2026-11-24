# Graceful scale-in for voice AI agents on Amazon ECS with task scale-in protection

Sample code for the AWS blog post of the same name. A capacity controller decides how many calling tasks run and which one to remove. Every task protects itself from scale-in when it starts. To remove a task, the controller does the following in order:

1. Stops new work on the task: the task stops dialing (in a full system, your dispatcher also stops assigning it calls; the sample has no dispatcher). It still serves any call the load balancer routes to it before deregistration, because that call ends at the call cap, before the deregistration delay does.
2. Waits for the calls the task has placed to connect. Their media streams arrive after the task starts draining, so the task tracks each one by the call ID it gave the call when it dialed.
3. Releases scale-in protection on that task only.
4. Lowers the desired count.

Amazon ECS then has exactly one task it can stop. The task's live calls finish within the target group's deregistration delay.

> This is sample code, not production code. The load balancer accepts connections only from `AllowedCidr`, and the calling task does not authenticate callers. For production, also verify each carrier request (for example, the carrier's request signature) before accepting a call, and turn on load balancer access logs to an S3 bucket you manage. The dialer is simulated: each placed call gets an unguessable call ID, and after `RING_SECONDS` a simulated carrier connects that call's media stream back to the same task at `/ws?call=<id>`, so the controller's wait-for-ringing step can be exercised without a telephony carrier. Calls are WebSocket connections held open through the Application Load Balancer.

## Contents

| Path | What it is |
|---|---|
| `app/calling_task.py` | Calling task: WebSocket calls, self-protection through the Amazon ECS agent endpoint, renewal while ACCEPTING, drain on request, SIGTERM handling |
| `app/controller.py` | Controller: `scale-in`, `scale-out`, `abort`, `status`. It is the only writer of the desired count, and each step is persisted to Valkey before the next one runs |
| `Dockerfile` | One image for both the calling task and the controller (ARM64, non-root) |
| `infra/service.yaml` | HTTPS Application Load Balancer, Amazon ElastiCache Serverless (Valkey), Amazon ECS cluster, calling service, controller task definition, and a KMS key for the logs |
| `infra/teardown.sh` | Sets the desired count to 0 (which blocks renewals), releases and verifies protection, waits for every task to stop, then deletes the stack, its task definitions and the cluster's Container Insights log group |
| `test/calls.py` | Simulated callers over `wss://`: hold long calls, or probe where new calls land |
| `test/run_controller.sh` | Runs one controller command as a Fargate task inside the VPC and prints its log |
| `tests/` | Unit tests (pytest) with an in-memory Amazon ECS fake and fakeredis; no AWS account needed |
| `requirements-dev.txt` | Pinned test dependencies |

## Prerequisites

- A VPC with two public subnets (for the load balancer) and two private subnets (for the tasks, the controller and the cache) in different Availability Zones. The private subnets need outbound access to AWS APIs, through a NAT gateway or VPC endpoints.
- An AWS Certificate Manager certificate for the HTTPS listener. Telephony carriers connect over `wss://`.
- Docker or Finch, with permission to push to Amazon ECR.
- Your telephony carrier's IP range for `AllowedCidr`. The template rejects `0.0.0.0/0`.

## Deploy

Run every command in this README from this folder (`agentic-workloads/voice-ai-ecs-graceful-scale-in`). Every resource is tagged `Article: Voice-ECS`.

```bash
export AWS_REGION=us-east-1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=voice-ecs-scale-in
aws ecr create-repository --repository-name $REPO --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=KMS --image-tag-mutability IMMUTABLE --tags Key=Article,Value=Voice-ECS
aws ecr get-login-password | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com
docker build --platform linux/arm64 -t $ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:v1 .
docker push $ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:v1

aws cloudformation deploy --stack-name voice-ecs-service --template-file infra/service.yaml \
  --capabilities CAPABILITY_IAM --tags Article=Voice-ECS \
  --parameter-overrides ImageUri=$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:v1 \
    VpcId=<vpc-id> PublicSubnetIds=<subnet-a>,<subnet-b> PrivateSubnetIds=<subnet-c>,<subnet-d> \
    CertificateArn=<acm-certificate-arn> AllowedCidr=<carrier-cidr>
```

The service starts with one task. The template leaves `DesiredCount` out on purpose: CloudFormation sends a templated count on every stack update that touches the service, which would overwrite the count the controller set. Scale out with the controller instead:

```bash
./test/run_controller.sh scale-out   # repeat until the service has the capacity you need
```

The defaults follow the post: a 600-second call cap, a 900-second deregistration delay, `stopTimeout` 120 s, protection for 15 minutes renewed every 5 minutes, and a 120-second health check grace period. To reproduce the post's test runs quickly, override `CallCapSeconds=90 DeregistrationDelaySeconds=180 RenewSeconds=60`.

`CallCapSeconds` must be shorter than `DeregistrationDelaySeconds`, or a call that connects just before deregistration can be cut off when Amazon ECS stops the task. CloudFormation can't compare two parameters, so the calling task checks this at startup: if the rule doesn't hold, it logs `config_invalid` and exits before it protects itself or takes a call, and the service doesn't reach a steady state.

## Run

`test/calls.py` needs the packages from `requirements-dev.txt` (see [Run the tests](#run-the-tests)). If the listener uses a private or self-signed certificate, set `CA_FILE` to its PEM file.

```bash
python test/calls.py hold <certificate-host-name> 9 calls.jsonl &   # nine long calls
./test/run_controller.sh status
./test/run_controller.sh scale-in
```

### Interrupted removals

Each scale-in step is recorded in Valkey, so running `scale-in` again resumes an interrupted removal. Until a removal has lowered the count, `scale-out` refuses to run, because raising the count would make the removal's stored target stale; run `abort` instead, which hands the task back at once. Once Amazon ECS has started stopping the removed task, `scale-out` works again. If a resumed `scale-in` exits because the desired count changed since the task was nominated, or you decide not to remove the task after all, back the removal out:

```bash
./test/run_controller.sh abort
```

`abort` works only before the count was lowered, while the task is `DRAINING` or `UNPROTECTED` and still running. It re-protects the task (UpdateTaskProtection, 15 minutes by default), verifies the protection, clears the removal's target fields and sets the task back to `ACCEPTING`. The task then accepts calls, dials and renews its own protection again. Once the count has been lowered, `abort` refuses; run `scale-in` to finish the removal instead.

## Run the tests

The unit tests need no AWS account and make no AWS calls: `tests/fake_ecs.py` stands in for the Amazon ECS API (including scale-in protection, `failures[]` responses, pagination and a task that keeps draining after its desired status is `STOPPED`), and fakeredis stands in for Valkey. Some tests start a WebSocket server on `127.0.0.1` only.

```bash
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

To run the same tests against another checkout, set `APP_REPO` to its path, for example `APP_REPO=../older-checkout python -m pytest tests`.

## What to watch

- A `protection_failed` log line with `DEPLOYMENT_BLOCKED` means the service has more protected tasks than its desired count. Amazon ECS refuses every renewal until that clears. `controller.py status` reports this as `surplus_tasks`. Resolve it by draining a task you choose before protection expires on its own.
- The load balancer health check reports process health only. A task that fails it is replaced even while protected.
- Availability Zone rebalancing is turned off on the service (`AvailabilityZoneRebalancing: DISABLED`). A rebalance starts an extra task, which protects itself as soon as it starts, so the service could end up with more protected tasks than its desired count. Amazon ECS then can't stop any of them and refuses every renewal with `DEPLOYMENT_BLOCKED`. Use `scale-in` to move capacity out of an Availability Zone instead.

## Limitations

- **A placed call's media stream must reach the task that placed it.** The sample assumes the carrier's stream for a call arrives at the task that dialed it. A load balancer listener rule can't route to a single task, and the tasks run in private subnets, so a real deployment typically needs a small proxy that looks up the call ID and forwards the stream to the placing task; the sample doesn't build that routing. The simulated carrier passes the call ID as a query string (`/ws?call=<id>`). Twilio stream URLs don't accept a query string, so with Twilio carry the ID in the URL path: a `<Parameter>` value arrives only in the stream's `start` message, after the connection opens.
- **A task that can't confirm its protection reports healthy but takes no calls.** The task retries its first protection call for 60 seconds (`INITIAL_PROTECT_WINDOW_SECONDS`), then keeps retrying in the background. Until protection is confirmed, it stays `STARTING`: `/health` returns 200, so Amazon ECS doesn't replace it, but it refuses every `/ws` connection with HTTP 503, and the controller treats it as starting and doesn't scale in. The load balancer still routes some new calls to it, and those calls fail. To take such a task out of rotation instead, return 503 from `/health` until protection is confirmed; Amazon ECS then replaces it once the health check grace period (120 seconds) ends.
- **Valkey is a single coordination dependency, and a lost write isn't repaired.** Valkey calls time out after `VALKEY_TIMEOUT_SECONDS` (5 seconds), and a task keeps its last known state when a read fails. The controller fails closed: a Valkey error ends the run, and the next run resumes from the persisted state. A task, however, records itself as `ACCEPTING` only once, when it answers its first load balancer health check. If that write fails, nothing retries it: the task serves calls, but the controller never counts it as accepting and refuses to scale in until the task restarts. For production, retry that write without overwriting a state the controller has set, and alarm on Valkey errors.
- Valkey has no authentication in this sample; access is limited by security groups only.
- An image or task definition update rolls slowly: every task is protected, so old tasks can't be stopped until they are drained or their protection lapses.

## Clean up

```bash
IMAGE_REPO=voice-ecs-scale-in ./infra/teardown.sh
```

The script first sets the desired count to 0, so protection renewals fail with `DEPLOYMENT_BLOCKED`. It then releases protection on every task, verifies with `get-task-protection` (releasing again if a renewal got in first), and waits until `DescribeTasks` reports every task as `STOPPED`, which takes about the deregistration delay. Only then does it delete the stack, followed by the task definition revisions the stack created and the cluster's Container Insights log group, which Amazon ECS creates outside the stack (and nothing else in the account). If a task is still protected after `RELEASE_ATTEMPTS` (10) attempts, or doesn't stop within `STOP_WAIT_SECONDS`, it exits without deleting anything.

The script ends by listing resources still tagged `Article: Voice-ECS`. A non-empty list right after teardown is expected: the tagging API keeps listing deleted resources (stopped tasks, the inactive cluster, the service, task definitions) for a while. To confirm the clean up, check instead that:

- the stack is `DELETE_COMPLETE`: `aws cloudformation describe-stacks --stack-name voice-ecs-service` reports that the stack does not exist, or `list-stacks` shows it as `DELETE_COMPLETE`;
- the ECR repository is gone: `aws ecr describe-repositories --repository-names voice-ecs-scale-in` returns `RepositoryNotFoundException`.

The stack's KMS key (for the log group) isn't deleted at once. CloudFormation schedules it for deletion with a 7-day waiting period (`PendingWindowInDays: 7`), so it shows as `PendingDeletion` until then. During that window you can still cancel the deletion with `aws kms cancel-key-deletion`.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This sample is licensed under the MIT-0 License. See the repository root [LICENSE](../../LICENSE) file.
