# Nut Pouring Pipeline

AWS OSMO reproduction of NVIDIA OSMO's upstream nut pouring cookbook. This is a
multi-stage pipeline rather than a single workflow: MimicGen creates synthetic
demonstrations, Isaac Lab converts HDF5 to MP4, Cosmos Transfer augments the
camera stream, Isaac Lab converts the augmented videos back to HDF5, the dataset
is converted to LeRobot format, and GR00T-N1.5 is fine-tuned.

Upstream source:

- <https://github.com/NVIDIA/OSMO/tree/main/cookbook/nut_pouring>
- Pinned ref: `c2c30e55f84969fff55d51cd2044a03d40d6a1a5`

Files:

- [workflows/](workflows/): prepared six-step OSMO workflow set.
- [workflows/README.md](workflows/README.md): upstream cookbook README copied
  from NVIDIA OSMO.
- [artifacts/](artifacts/): representative policy rollout GIF.

## Adaptation Notes

Adapted from
<https://github.com/NVIDIA/OSMO/tree/main/cookbook/nut_pouring> at
`NVIDIA/OSMO@c2c30e55f84969fff55d51cd2044a03d40d6a1a5`, with the following
changes:

- target the the sample's `g7e-rtx-pro-6000` OSMO platform and
  200Gi ephemeral storage;
- normalize OSMO 6.2 dataset shorthand and mounted dataset paths;
- remove the upstream interactive `sleep infinity` hold from step 1;
- avoid printing Hugging Face tokens while logging into the Cosmos container;
- pin the Cosmos Transfer checkout and the Cosmos Predict tokenizer revision;
- flatten Cosmos output MP4s to Isaac Lab's `demo_{id}_*.mp4` convention before
  `mp4_to_hdf5.py`;
- use a GR1-aware MP4-to-HDF5 helper because the upstream converter expects
  `obs/eef_pos`, while the GR1 dataset stores `left_eef_pos` and
  `right_eef_pos`;
- repair Isaac Lab `pip` after GR00T dependency installation for LeRobot
  conversion;
- use CUDA 12.8 PyTorch wheels and SDPA attention fallback for Blackwell/G7e;
- collect GR00T training GPU metrics, TensorBoard logs, a run manifest, and
  retain training checkpoints in the output dataset.

Run from the repository root:

```bash
cd ai-infra/robotics-foundation-models-on-eks
TF_OUTPUT_AWS_REGION=ap-northeast-2 \
TF_OUTPUT_CLUSTER_NAME=example-osmo-eks \
TF_OUTPUT_OSMO_NAMESPACE=osmo \
TF_OUTPUT_OSMO_WORKLOAD_NAMESPACE=osmo-workflows \
TF_OUTPUT_OSMO_RUNTIME_SECRET_ARN='example-osmo/runtime' \
NUT_POURING_SKIP_DATASET_UPLOAD=true \
NUT_POURING_START_STEP=4 \
NUT_POURING_PREWARM_INSTANCE_TYPE=g7e.24xlarge \
HF_TOKEN_FILE="$HOME/.huggingface/token" \
examples/simulation/nut-pouring-pipeline/run.sh
```

Use `NUT_POURING_START_STEP=1` to run from the original teleoperation HDF5
upload through final GR00T fine-tuning. The committed validation starts at step
4 because the stage 1-3 datasets were already present from prior nut pouring
reproduction work and stage 3 is a long Cosmos Transfer workload.

The wrapper keeps the upstream cookbook behavior intact where possible. The AWS
OSMO preparation layer only applies the compatibility and evidence-capture
changes listed above.

The public runner submits the prepared workflow files committed under
[workflows/](workflows/) instead of fetching and patching upstream OSMO at
runtime. Use `NUT_POURING_WORKFLOWS_DIR=/path/to/workflows` only when testing a
locally refreshed workflow set.
