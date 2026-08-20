# EC2 Setup Check

After Terraform creates the instance, SSH in and run:

```bash
cd <repo>/v1/infrastructure
./verify-ec2.sh <repo>
```

If you have not cloned the repo yet, you can still run:

```bash
./verify-ec2.sh
```

What it checks:

- `python3`, `aws`, `git`
- `~/IsaacSim/python.sh`
- `~/IsaacSim/setup_ros_env.sh`
- `/opt/ros/jazzy/setup.bash`
- `~/workspace/PX4-Autopilot`
- Pegasus extension under `~/.local/share/ov/data/exts/v2/`
- AWS credentials via `aws sts get-caller-identity`
- optional repo-local `v1` launchers and backend venv

Exit codes:

- `0`: machine is basically ready
- `1`: one or more hard failures found

Warnings do not fail the script, but they usually mean you still have setup work left.
