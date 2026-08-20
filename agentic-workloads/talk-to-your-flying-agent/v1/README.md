# v1

Minimal run order for the extracted stack.

## Before you run anything

Make sure `v1/infrastructure` has already been applied and the EC2 is up.

The commands below are meant to be run **inside the EC2** after you SSH in or
open the DCV desktop on that machine.

For more detail, refer to:

- [infrastructure/README.md](infrastructure/README.md) for Terraform, DCV, and EC2 setup
- [simulation/drone/README.md](simulation/drone/README.md) for simulation details
- [app/backend/README.md](app/backend/README.md) for backend details

## Terminal 1: Simulation first

```bash
cd v1/simulation/drone
./scripts/run_simulation.sh --config warehouse_shelves
```

Wait until the sim log shows `Ready for takeoff!`.

## Terminal 2: Backend second

First time only:

```bash
cd v1/app/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then start the backend:

```bash
cd v1/app/backend
./run-api.sh
```

## Terminal 3: Optional manual flight

```bash
cd v1/simulation/drone
python3 ./scripts/keyboard_fly.py
```

## Notes

- Start the sim before the backend.
- The backend expects PX4 on `udp://:14540` and ROS 2 topics from the sim.
- Machine-level dependencies still need to exist outside the repo: Isaac Sim,
  Pegasus, ROS 2 Jazzy, PX4, and AWS Bedrock credentials.
