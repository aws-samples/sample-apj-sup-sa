"""Central configuration for Talk to your Video.

Everything is env-overridable so the demo can be re-pointed without code edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- Directories -------------------------------------------------------------
VIDEO_DIR = Path(os.getenv("TALK2VID_VIDEO_DIR", ROOT / "videos"))
DATA_DIR = Path(os.getenv("TALK2VID_DATA_DIR", ROOT / "data"))
INDEX_DIR = DATA_DIR / "index"
CACHE_DIR = DATA_DIR / "cache"
CLIENT_DIR = ROOT / "client"

for _d in (VIDEO_DIR, INDEX_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- AWS ---------------------------------------------------------------------
AWS_REGION = os.getenv("TALK2VID_AWS_REGION", os.getenv("AWS_REGION", "us-east-1"))

# Conversational brain: fast, tool-capable, supports Bedrock prompt caching.
LLM_MODEL_ID = os.getenv("TALK2VID_LLM_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
# Frame/segment level visual reasoning (accepts IMAGE + VIDEO input).
VISION_MODEL_ID = os.getenv("TALK2VID_VISION_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
# Whole-video understanding (TwelveLabs, video-native).
PEGASUS_MODEL_ID = os.getenv("TALK2VID_PEGASUS_MODEL_ID", "twelvelabs.pegasus-1-2-v1:0")
# Unified multimodal embeddings: async segmented for video, sync for text queries.
EMBED_MODEL_ID = os.getenv("TALK2VID_EMBED_MODEL_ID", "amazon.nova-2-multimodal-embeddings-v1:0")
EMBED_DIM = int(os.getenv("TALK2VID_EMBED_DIM", "1024"))

S3_BUCKET = os.getenv("TALK2VID_S3_BUCKET", "")  # set by scripts/provision_aws.py
S3_PREFIX = os.getenv("TALK2VID_S3_PREFIX", "talk2vid")

# --- Voice pipeline ----------------------------------------------------------
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DAILY_API_KEY = os.getenv("DAILY_API_KEY", "")

# Outside-world knowledge runs on an Amazon Bedrock AgentCore harness (managed
# agent loop + Gateway + Amazon's web index). Set by scripts/provision_agentcore.py.
AGENTCORE_HARNESS_ARN = os.getenv("TALK2VID_HARNESS_ARN", "")

STT_MODEL = os.getenv("TALK2VID_STT_MODEL", "nova-3")

# Noisy-room guard: the longest a single spoken turn may run. Background chatter
# keeps VAD in the SPEAKING state, and the turn controller refuses to finalize a
# turn while it still hears speech, so the only way out is for the audio to stop:
# the browser mutes the mic at this point, which ends the turn and gets the
# question answered. Served to the client from /api/health.
MAX_LISTEN_SECS = float(os.getenv("TALK2VID_MAX_LISTEN_SECS", "5"))
# Watchdog for a turn whose end-of-turn analysis never reports "complete" — which
# is exactly what a sentence cut off by the cap above looks like. Pipecat's 5s
# default is a long silence to leave hanging on stage.
TURN_STOP_TIMEOUT_SECS = float(os.getenv("TALK2VID_TURN_STOP_TIMEOUT", "2.0"))
TTS_VOICE = os.getenv("TALK2VID_TTS_VOICE", "aura-2-thalia-en")
TTS_SAMPLE_RATE = int(os.getenv("TALK2VID_TTS_SAMPLE_RATE", "24000"))

# --- Server ------------------------------------------------------------------
HOST = os.getenv("TALK2VID_HOST", "127.0.0.1")
PORT = int(os.getenv("TALK2VID_PORT", "7860"))

# Media transport:
#   "webrtc" — peer-to-peer to the browser. Lowest latency, laptop-local demos.
#   "daily"  — media rides Daily's infrastructure. Needed once the server is
#              behind CloudFront/an ALB, where inbound UDP does not exist.
TRANSPORT = os.getenv("TALK2VID_TRANSPORT", "webrtc").lower()
DAILY_ROOM_MINUTES = int(os.getenv("TALK2VID_DAILY_ROOM_MINUTES", "30"))

# --- Ingest tuning -----------------------------------------------------------
SEGMENT_TARGET_SECS = float(os.getenv("TALK2VID_SEGMENT_TARGET", "12"))
SEGMENT_MIN_SECS = float(os.getenv("TALK2VID_SEGMENT_MIN", "6"))
SEGMENT_MAX_SECS = float(os.getenv("TALK2VID_SEGMENT_MAX", "20"))
SCENE_THRESHOLD = float(os.getenv("TALK2VID_SCENE_THRESHOLD", "0.06"))  # floor; scores are ranked
EMBED_SEGMENT_SECS = int(os.getenv("TALK2VID_EMBED_SEGMENT_SECS", "15"))  # Nova MME max 30
CAPTION_CONCURRENCY = int(os.getenv("TALK2VID_CAPTION_CONCURRENCY", "8"))


@dataclass
class Scenario:
    """Presentation metadata for a demo video."""

    key: str
    label: str
    accent: str
    persona: str = ""
    starters: list[str] = field(default_factory=list)


SCENARIOS: dict[str, Scenario] = {
    "cloud": Scenario(
        key="cloud",
        label="Cloud & Security",
        accent="#ff9900",
        persona=(
            "This is technical AWS content. Be precise with service names, architecture "
            "and security controls. Prefer exact quotes over paraphrase when the speaker "
            "states a fact."
        ),
        starters=[
            "What is this video about?",
            "Which AWS services are mentioned?",
            "Show me where they talk about security",
        ],
    ),
    "healthcare": Scenario(
        key="healthcare",
        label="Healthcare",
        accent="#4fc3f7",
        persona=(
            "This is a recorded clinical tele-consultation. Be careful and literal: report "
            "only what was actually said or shown. Never invent symptoms, dosages or "
            "diagnoses, and add no medical advice of your own. If asked for advice, say you "
            "can only summarise what is in the recording."
        ),
        starters=[
            "Summarise the consultation",
            "What symptoms did the patient report?",
            "What follow-up was agreed?",
        ],
    ),
    "sports": Scenario(
        key="sports",
        label="Sports & Media",
        accent="#69f0ae",
        persona=(
            "This is sports footage. Be lively but factual. Use the visual track for "
            "player actions and on-screen graphics; use the audio track for commentary."
        ),
        starters=[
            "What happens in this clip?",
            "Show me the goal",
            "Who is on the ball at the start?",
        ],
    ),
    "education": Scenario(
        key="education",
        label="Education",
        accent="#ffd166",
        persona=(
            "This is instructional or assessment content. Be precise about technique, criteria "
            "and phrasing, and quote the speaker's exact wording when the wording is the point. "
            "If asked to evaluate, judge only against what is actually said or shown."
        ),
        starters=[
            "Summarise what happens here",
            "What makes this a strong answer?",
            "Show me where they talk about the topic",
        ],
    ),
    "tech": Scenario(
        key="tech",
        label="Tech & Robotics",
        accent="#a78bfa",
        persona=(
            "This is a technology talk or demo. Be concrete about what the hardware or software "
            "actually does on screen, and separate what is demonstrated from what is claimed."
        ),
        starters=[
            "What is being demonstrated?",
            "Show me the most impressive moment",
            "What is the robot doing right now?",
        ],
    ),
    "general": Scenario(
        key="general",
        label="General",
        accent="#b388ff",
        persona="",
        starters=["What is this video about?", "Give me the highlights"],
    ),
}


def scenario(key: str) -> Scenario:
    return SCENARIOS.get(key, SCENARIOS["general"])
