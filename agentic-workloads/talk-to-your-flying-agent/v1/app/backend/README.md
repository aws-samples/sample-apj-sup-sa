# Backend

Extracted backend capability from `isaac_sim/server`, focused on the
`run-api.sh` runtime path.

## What is here

- `run-api.sh` — FastAPI launcher on port `8888`
- `api/` — command API, Strands agent, MAVSDK control, ROS sensor bridge,
  Bedrock perception, session recorder
- `static/index.html` — current lightweight frontend
- `sessions/` — session recordings and captured decision frames
- `scripts/eval_report.py`, `scripts/eval_rollup.py` — post-run eval helpers

## What this backend does

- Serves the web UI at `/`
- Accepts chat commands at `POST /send-command`
- Connects to PX4 over MAVSDK on `udp://:14540`
- Reads RGB, depth, and lidar from ROS 2 topics
- Runs Strands agents for routing and planning
- Calls AWS Bedrock for planner/router/vision models
- Streams camera and depth MJPEG endpoints
- Records sessions to `sessions/<session-id>/`

## Python dependencies

Install into a venv inside this folder:

```bash
cd v1/app/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## External runtime dependencies

These are required but not vendored into this folder:

- ROS 2 Jazzy at `/opt/ros/jazzy`
- A running PX4-compatible sim publishing MAVSDK on `udp://:14540`
- ROS topics from the sim:
  - `/drone1/camera/color/image_raw`
  - `/drone1/camera/depth`
  - `/drone1/lidar/laserscan`
- AWS credentials with `bedrock:InvokeModel`

In this repo, the intended sim-side pair is [simulation/drone](../../simulation/drone).

## Launch

```bash
cd v1/app/backend
./run-api.sh
```

## Notes

- `run-api.sh` sources ROS 2 before starting Python because `rclpy` needs the
  ROS environment available at process start.
- The checked-in source `isaac_sim/server/requirements.txt` was incomplete for
  this runtime path. This extracted copy includes a corrected dependency list.
