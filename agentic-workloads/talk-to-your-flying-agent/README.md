# Talk to Your Flying Agent

General-purpose AI pilot for a simulated drone: natural-language missions,
human-in-the-loop control, and a browser UI on top of Isaac Sim + PX4 + ROS 2.

## What this project is

The core idea is:

- agents for hardware, not just software
- a drone you can task in plain English
- perception + planning + flight control working together
- human pause/resume when the mission needs guidance

Current active stack:

- simulation: Isaac Sim + Pegasus + PX4 + ROS 2
- backend: FastAPI + Strands agents + Bedrock models
- infrastructure: Terraform for the GPU EC2 / DCV base machine

## Where to start

`v1/` is the canonical current version.

Read these in order:

1. [v1/README.md](v1/README.md)
2. [v1/infrastructure/README.md](v1/infrastructure/README.md)

## Active layout

- [v1/simulation/drone](v1/simulation/drone) — sim runtime
- [v1/app/backend](v1/app/backend) — backend runtime
- [v1/infrastructure](v1/infrastructure) — EC2 / DCV provisioning

## License

See [LICENSE.txt](LICENSE.txt).
