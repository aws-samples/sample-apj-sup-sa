# Infrastructure

Terraform for the `v1` runtime base machine: GPU EC2, IAM, security group,
Elastic IP, and Bedrock invocation logging.

This package provisions the box. It does **not** fully install the app stack.
After the EC2 is up, you still need to get this repo onto the instance and run
the `v1` sim/backend commands.

## Fresh account checklist

Before Terraform:

- [ ] Subscribe to the Isaac Sim AMI in AWS Marketplace:
      `https://aws.amazon.com/marketplace/pp/prodview-bl35herdyozhw`
- [ ] Enable Bedrock model access in `ap-northeast-1` for the models used by
      `v1/app/backend/api/config.py`
- [ ] Create an EC2 key pair in the target region
- [ ] Know the public IP/CIDR you want to allow for SSH, DCV, and backend access
- [ ] Make sure local AWS credentials work:

```bash
aws sts get-caller-identity
```

## Remote state

Run:

```bash
cd v1/infrastructure
./setup-backend.sh
```

By default it creates:

- S3 bucket: `v1-tfstate-<ACCOUNT_ID>`
- DynamoDB table: `v1-tfstate-lock`
- state key: `v1/infrastructure/terraform.tfstate`

You can override those with environment variables or `v1/infrastructure/.env.dev`:

- `TF_STATE_BUCKET_PREFIX`
- `TF_STATE_LOCK_TABLE`
- `TF_STATE_KEY`
- `TF_STATE_REGION`

Optional:

```bash
cd v1/infrastructure
cp .env.dev.example .env.dev
```

`.env.dev` is local-only and gitignored.

## Deploy

```bash
cd v1/infrastructure
cp terraform.tfvars.example terraform.tfvars
# Edit at minimum: key_name, allowed_ips, instance_password

./setup-backend.sh
terraform init -backend-config=backend.tfbackend
terraform plan
terraform apply
```

`terraform.tfvars` and `backend.tfbackend` are local-only and gitignored. Do not
commit them; `terraform.tfvars` contains the DCV `instance_password`.

Useful outputs:

```bash
terraform output ssh_command
terraform output dcv_url
terraform output dcv_login_user
terraform output project_home
terraform output backend_url
```

## First login

- SSH into the instance
- Open DCV in the browser using `terraform output dcv_url`
- Log in with user `terraform output dcv_login_user` and the `instance_password`
  you set in `terraform.tfvars`

Optional first-time warmup:

```bash
cd ~/IsaacSim
./warmup.sh
```

## Put the repo on the box

Clone or copy this repo onto the EC2.

Example:

```bash
cd ~
git clone <your-repo-url>
```

## Important variables

- `aws_region`: workload region, default `ap-northeast-1`
- `instance_type`: default `g6e.8xlarge`
- `key_name`: required EC2 key pair name
- `allowed_ips`: required CIDRs for SSH/DCV/API
- `project_name`: prefixes resource names
- `instance_user`: default `ubuntu`
- `instance_password`: required, used for DCV login
- `project_home`: folder created on the instance
- `ssh_port`, `dcv_port`, `api_port`
- `bedrock_log_group_name`

## What gets provisioned

- Isaac Sim marketplace EC2
- security group with SSH, DCV, backend API ingress
- IAM role + instance profile for Bedrock
- Bedrock invocation logging to CloudWatch with 365-day retention
- optional Elastic IP

## What this does not automate

This package does **not** currently:

- clone the repo onto the EC2
- create `v1/app/backend/.venv`
- install PX4
- install or patch Pegasus
- install `Micro-XRCE-DDS-Agent`
- warm Isaac Sim shaders

So the real flow is:

1. `terraform apply`
2. SSH / DCV into the box
3. put this repo on the instance
4. run the app stack from [../README.md](../README.md)

## Useful outputs

- `terraform output ssh_command`
- `terraform output dcv_url`
- `terraform output dcv_login_user`
- `terraform output backend_url`
- `terraform output project_home`

## Quick EC2 readiness check

After SSHing into the instance and getting the repo onto the box:

```bash
cd <repo>/v1/infrastructure
./verify-ec2.sh <repo>
```

See [EC2_SETUP_CHECK.md](EC2_SETUP_CHECK.md).

## Required runtime checks

Verify these exist on the EC2 before trying to run `v1`:

```bash
ls ~/IsaacSim
ls ~/workspace/PX4-Autopilot
ls ~/.local/share/ov/data/exts/v2 | grep pegasus
ls /opt/ros/jazzy
```

If any of those are missing, the machine is provisioned but the runtime is not
ready yet.

## Run v1 on the EC2

Terminal 1, sim first:

```bash
cd <repo>/v1/simulation/drone
./scripts/run_simulation.sh --config warehouse_shelves
```

Wait for:

```text
Ready for takeoff!
```

Terminal 2, backend second:

```bash
cd <repo>/v1/app/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run-api.sh
```

Terminal 3, optional manual flight:

```bash
cd <repo>/v1/simulation/drone
python3 ./scripts/keyboard_fly.py
```

## Smoke tests

Backend health:

```bash
curl http://localhost:8888/status
```

ROS topics:

```bash
source /opt/ros/jazzy/setup.bash
ros2 topic list | grep drone
```

Backend from your laptop:

```text
http://<instance-ip>:8888
```

Simple command:

```bash
curl -X POST http://localhost:8888/send-command \
  -H "Content-Type: application/json" \
  -d '{"text":"takeoff"}'
```

Land after test:

```bash
curl -X POST http://localhost:8888/send-command \
  -H "Content-Type: application/json" \
  -d '{"text":"land"}'
```

## Pass criteria

- Terraform applies successfully
- SSH works
- DCV opens
- Isaac Sim launches
- Sim reaches `Ready for takeoff!`
- Backend starts on `:8888`
- `GET /status` returns JSON
- `takeoff` and `land` succeed
- Bedrock-backed commands do not fail with access or model errors

## Likely failure points

- Isaac Sim AMI subscription missing
- Bedrock model access missing in the new account
- `allowed_ips` wrong because your IP changed
- PX4 missing at `~/workspace/PX4-Autopilot`
- Pegasus missing under `~/.local/share/ov/data/exts/v2/`
- ROS 2 missing at `/opt/ros/jazzy`

## Notes

- The backend listens on `api_port` (default `8888`).
- Bedrock invocation logging is account-wide in the workload region.
- The EC2 bootstrap is intentionally small: it prepares the machine, not the
  whole repo runtime.
