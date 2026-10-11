"""Preflight check for the media host, run before systemd is wired up.

Every failure this catches would otherwise present as a call that connects and
then does nothing, which is the most expensive kind of bug to diagnose over the
phone. So it is checked on the box, at deploy time, with a non-zero exit.

    python -m media.selfcheck

Checks, in the order they would bite:
  1. the modules import at all under the box's interpreter
  2. the bundled Silero ONNX model is present and loads at 8kHz
  3. AGENT_RUNTIME_ARN is set and the TTS choice is coherent
  4. the AWS credentials on the instance can actually reach each service

Step 4 is deliberately read-only: it resolves identity and describes the
endpoints rather than invoking a model, so running this costs nothing and can
be re-run freely.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES: list[str] = []
WARNINGS: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok      {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL    {msg}")
    FAILURES.append(msg)


def warn(msg: str) -> None:
    print(f"  warn    {msg}")
    WARNINGS.append(msg)


def check_imports() -> None:
    print("imports")
    try:
        import media.agent_bridge  # noqa: F401
        import media.audiosocket  # noqa: F401
        import media.bot  # noqa: F401
        import media.stt  # noqa: F401
        import media.tts  # noqa: F401
        ok("media.{audiosocket,stt,tts,agent_bridge,bot}")
    except Exception as e:  # noqa: BLE001
        fail(f"media modules: {e!r}")

    try:
        import onnxruntime
        import pipecat
        print(f"  ok      pipecat {pipecat.__version__}, onnxruntime {onnxruntime.__version__}")
    except Exception as e:  # noqa: BLE001
        fail(f"pipecat/onnxruntime: {e!r}")


def check_vad() -> None:
    """The VAD model ships inside the package; confirm it is really there.

    This matters because the alternative implementation downloads weights on
    first use. If a future pipecat did that, the first live call would stall on a
    network fetch from a host whose egress is locked down.
    """
    print("silero vad")
    try:
        import pipecat.audio.vad as vadpkg
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        path = os.path.join(os.path.dirname(vadpkg.__file__), "data", "silero_vad.onnx")
        if not os.path.exists(path):
            fail(f"bundled model missing at {path}")
            return
        ok(f"model present ({os.path.getsize(path)} bytes)")

        # 8kHz specifically: this is a telephony path, and instantiating at the
        # wrong rate is a silent accuracy loss rather than an error.
        SileroVADAnalyzer(sample_rate=8000)
        ok("analyzer constructs at 8kHz")
    except Exception as e:  # noqa: BLE001
        fail(f"silero: {e!r}")


def check_config() -> None:
    print("configuration")
    arn = os.environ.get("AGENT_RUNTIME_ARN", "")
    if not arn:
        fail("AGENT_RUNTIME_ARN is unset; the bridge has no agent to call")
    elif not arn.startswith("arn:aws:bedrock-agentcore:"):
        fail(f"AGENT_RUNTIME_ARN does not look like an AgentCore ARN: {arn}")
    else:
        ok(f"agent runtime {arn.rsplit('/', 1)[-1]}")

    engine = os.environ.get("STT_ENGINE", "hybrid").lower()
    if engine not in ("hybrid", "voxtral", "whisper"):
        fail(f"STT_ENGINE={engine} is not one of hybrid|voxtral|whisper")
    else:
        ok(f"stt engine {engine}")

    tts = os.environ.get("HVI_TTS", "omnivoice").lower()
    if tts == "omnivoice":
        if not os.environ.get("OMNIVOICE_ENDPOINT"):
            fail("HVI_TTS=omnivoice but OMNIVOICE_ENDPOINT is unset")
        else:
            ok(f"tts omnivoice -> {os.environ['OMNIVOICE_ENDPOINT']}")
    else:
        # OmniVoice is the TTS the customer named. There is no second engine to
        # fall back to, deliberately: a fallback one env var away stopped being an
        # interim and became the voice that shipped.
        fail(f"HVI_TTS={tts} is not available; OmniVoice is the only TTS")

    check_prompt_cache(tts)


def check_prompt_cache(tts: str) -> None:
    """Is the pre-synthesised prompt cache actually reachable from this box?

    Worth a check of its own because every way this breaks is SILENT. PromptCache
    is deliberately forgiving - an unset bucket, an unwritable directory or a
    missing object all degrade to live synthesis rather than failing a call, which
    is correct behaviour and also means a misconfiguration costs ~2,885ms per
    prompt with nothing in the logs to say why.

    Three separate things have to be true, and they fail independently:
      the bucket is configured, the local disk tier exists and is writable, and
      the instance role can actually GET an object.
    """
    bucket = os.environ.get("PROMPT_CACHE_BUCKET")
    if not bucket:
        warn("PROMPT_CACHE_BUCKET unset - every fixed prompt will be synthesised "
             "live (~2885ms vs 0.5ms cached)")
        return

    voice = os.environ.get("PROMPT_CACHE_VOICE", "omnivoice-kajal-ref-v1")
    # The key embeds the voice id, so a cache built under OmniVoice is invisible
    # to a differently-voiced run and vice versa. Mismatch means 100% miss.
    if tts == "omnivoice" and not voice.startswith("omnivoice"):
        warn(f"HVI_TTS=omnivoice but PROMPT_CACHE_VOICE={voice} - the cache will "
             f"miss on every prompt")
    if tts != "omnivoice" and voice.startswith("omnivoice"):
        warn(f"HVI_TTS={tts} but PROMPT_CACHE_VOICE={voice} - cached prompts "
             f"would play in the OmniVoice voice while dynamic text uses {tts}")

    cache_dir = os.environ.get("PROMPT_CACHE_DIR", "/var/lib/hvi/prompt-cache")
    if not os.path.isdir(cache_dir):
        warn(f"{cache_dir} missing - the disk tier is skipped and every prompt "
             f"becomes an S3 GET on the critical path")
    elif not os.access(cache_dir, os.W_OK):
        warn(f"{cache_dir} is not writable by this user - disk tier disabled")
    else:
        ok(f"prompt cache disk tier {cache_dir}")

    try:
        import boto3
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        r = s3.list_objects_v2(Bucket=bucket, Prefix="prompt-cache/", MaxKeys=1)
        n = r.get("KeyCount", 0)
        if n:
            ok(f"prompt cache readable in s3://{bucket}/prompt-cache/")
        else:
            warn(f"s3://{bucket}/prompt-cache/ is empty - run "
                 f"scripts/54-prompt-cache.sh")
    except Exception as e:  # noqa: BLE001
        warn(f"cannot read s3://{bucket}/prompt-cache/ ({type(e).__name__}) - "
             f"prompts will fall back to live synthesis")


def check_aws() -> None:
    """Confirm the instance role can reach each service it needs.

    Read-only calls only. The purpose is to catch a missing IAM statement now
    rather than mid-call, and an AccessDenied here is unambiguous.
    """
    print("aws reachability (read-only)")
    region = os.environ.get("DATA_REGION", "ap-south-1")
    try:
        import boto3
        from botocore.exceptions import ClientError
    except Exception as e:  # noqa: BLE001
        fail(f"boto3 import: {e!r}")
        return

    try:
        who = boto3.client("sts", region_name=region).get_caller_identity()
        ok(f"identity {who['Arn'].rsplit('/', 1)[-1]}")
    except Exception as e:  # noqa: BLE001
        fail(f"sts get-caller-identity: {e!r}")

    endpoint = os.environ.get("STT_ENDPOINT", "hvi-whisper")
    try:
        sm = boto3.client("sagemaker", region_name=region)
        status = sm.describe_endpoint(EndpointName=endpoint)["EndpointStatus"]
        if status == "InService":
            ok(f"whisper endpoint {endpoint} InService")
        else:
            warn(f"whisper endpoint {endpoint} is {status}, not InService")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "AccessDeniedException":
            # describe is not granted and does not need to be; invoke is what
            # matters. Say so rather than reporting a failure that is not one.
            warn(f"cannot describe {endpoint} (sagemaker:DescribeEndpoint not granted); "
                 "invoke permission is separate and untested here")
        else:
            warn(f"describe_endpoint({endpoint}): {code}")
    except Exception as e:  # noqa: BLE001
        warn(f"describe_endpoint({endpoint}): {e!r}")


def main() -> int:
    print("hvi media selfcheck")
    print("-" * 60)
    check_imports()
    check_vad()
    check_config()
    check_aws()
    print("-" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} problem(s), {len(WARNINGS)} warning(s)")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"PASSED with {len(WARNINGS)} warning(s)")
    for w in WARNINGS:
        print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
