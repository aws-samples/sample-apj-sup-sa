# SageMaker BYOC (Bring Your Own Container) — custom training image
#
# Key point: for training, SageMaker runs the container as 'docker run <image> train'.
#   → An executable named 'train' must be on the PATH with execute permission.
#   → No ENTRYPOINT is set, so the 'train' (training) / 'serve' (inference) argument
#     SageMaker appends becomes the command itself, letting one image handle both modes.

FROM public.ecr.aws/docker/library/python:3.11-slim

# Send logs to CloudWatch immediately, without buffering
ENV PYTHONUNBUFFERED=TRUE \
    PYTHONDONTWRITEBYTECODE=TRUE

# Packages needed for training (CPU build of torch — keeps the image small)
# To run on GPU (NVIDIA) instances: switch the base image to a CUDA-enabled one
# (nvidia/cuda or the AWS PyTorch GPU DLC) and replace the --index-url below with a CUDA build (e.g. .../whl/cu121).
# (--platform stays linux/amd64 even for GPU — see the comments in build_and_push.sh)
RUN pip install --no-cache-dir \
    torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cpu

# Put our program in /opt/program and add it to PATH
COPY train /opt/program/train
RUN chmod 755 /opt/program/train
ENV PATH="/opt/program:${PATH}"
WORKDIR /opt/program

# No ENTRYPOINT / CMD (intentional):
#   docker run <image> train  →  runs train (training)
#   docker run <image> serve  →  runs serve (inference — not included here;
#                                see "Next step: inference container" in the README)
