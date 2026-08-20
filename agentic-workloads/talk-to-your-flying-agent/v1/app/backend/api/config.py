"""Centralized config for the agent web app.

Single source of truth for values that might change or that other modules
need to read consistently: AWS regions, Bedrock model IDs, ROS 2 topic names,
sampling rates.

Every value supports an env-var override, so ops can tweak without editing
code. Defaults are the values used in dev.

See docs/PLAN.md DEC-009 for why Bedrock is pinned to Tokyo + explicit IDs.
"""

import os


# --- AWS regions ----------------------------------------------------------
# EC2 and Bedrock happen to be in the same region today, but they're
# conceptually independent — keep them as separate names so they can diverge.

EC2_REGION = os.getenv("EC2_REGION", "ap-northeast-1")
AWS_BEDROCK_REGION = os.getenv("AWS_BEDROCK_REGION", "ap-northeast-1")


# --- Bedrock model IDs ----------------------------------------------------
# All resolved in AWS_BEDROCK_REGION. Pinned per DEC-009.
#   Planner / single-model (today):  Opus 4.7 — swapped up from Sonnet 4.6
#                                    on 2026-04-29 for stronger reasoning at
#                                    the cost of higher latency + cost.
#                                    Roll back to Sonnet via env var if booth
#                                    latency suffers:
#                                      BEDROCK_MODEL_PLANNER=jp.anthropic.claude-sonnet-4-6
#   Sonnet (kept as a named fallback for quick rollback / split-model)
#   Router (Tier 2, once split):     Haiku 4.5
#   Vision (Tier 2, via boto3):      Qwen3 VL 235B
# Strands' BedrockModel supports Claude models. Qwen is called via raw
# bedrock-runtime.invoke_model (see Tier 2 notes in docs/TASKS.md).

BEDROCK_MODEL_PLANNER = os.getenv(
    "BEDROCK_MODEL_PLANNER", "jp.anthropic.claude-opus-4-7"
)
BEDROCK_MODEL_PLANNER_FALLBACK = os.getenv(
    "BEDROCK_MODEL_PLANNER_FALLBACK", "jp.anthropic.claude-sonnet-4-6"
)
BEDROCK_MODEL_ROUTER = os.getenv(
    "BEDROCK_MODEL_ROUTER", "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
)
BEDROCK_MODEL_VISION = os.getenv(
    "BEDROCK_MODEL_VISION", "qwen.qwen3-vl-235b-a22b"
)


# --- ROS 2 topic names ----------------------------------------------------
# Canonical topic names produced by Pegasus with namespace=drone1.
# Legacy `/drone/...` equivalents still exist; sensors.py subscribes to both
# for backward compatibility until the old scenes are retired.

ROS_CAMERA_TOPIC = os.getenv(
    "ROS_CAMERA_TOPIC", "/drone1/camera/color/image_raw"
)
ROS_DEPTH_TOPIC = os.getenv(
    "ROS_DEPTH_TOPIC", "/drone1/camera/depth"
)
ROS_LIDAR_TOPIC = os.getenv(
    "ROS_LIDAR_TOPIC", "/drone1/lidar/laserscan"
)


# --- Sampling / timing ----------------------------------------------------

CAMERA_SAMPLE_FPS = int(os.getenv("CAMERA_SAMPLE_FPS", "10"))
