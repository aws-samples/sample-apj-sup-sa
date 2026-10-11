"""Pre-synthesised speech cache: takes TTS off the critical path.

WHY THIS EXISTS
The turn budget was roughly 463ms STT + 600ms LLM + 1712ms TTS = 2.8s, and TTS
dominated it. But almost nothing the agent says is improvised: measured with
tools/prompt_inventory.py, 85 of 92 possible utterances contain no placeholders at
all. Synthesising those live, on every call, forever, was paying the largest cost
in the budget for text that never changes.

THREE CLASSES, DELIBERATELY SEPARATED
  shared        no placeholders. One object per (text, language), built once by
                tools/build_prompt_cache.py and reused by every call.
  personalised  contains {{customerName}} and friends. Cannot be shared, but is
                synthesised while the phone RINGS - dialling takes seconds and
                nothing is on the critical path until the callee answers.
  dynamic       genuinely model-generated. Falls through to live synthesis.

THE KEY IS A CONTENT HASH, AND THAT IS THE POINT
Keying on sha256(text | language | model) rather than on an item id means editing
a prompt in DynamoDB produces a different key, so stale audio is impossible - the
old object is simply never requested again. It also means a health item added at
runtime (a named requirement: new items without redeployment) is a cache MISS
rather than a wrong answer.

A MISS MUST NEVER FAIL A CALL. Because items are addable without redeployment the
cache will always potentially lag the schema, so a miss degrades to live synthesis
and, optionally, writes itself back so the second call is fast. A cache that could
fail a live call would be a worse trade than the latency it saves.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import threading
import wave

log = logging.getLogger("hvi.prompt_cache")

SAMPLE_RATE = 8000

BUCKET = os.environ.get("PROMPT_CACHE_BUCKET", "")
PREFIX = os.environ.get("PROMPT_CACHE_PREFIX", "prompt-cache")
LOCAL_DIR = os.environ.get("PROMPT_CACHE_DIR", "/var/lib/hvi/prompt-cache")
DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")

# Part of the cache key, so changing the voice or the model invalidates every
# entry rather than mixing two voices within one call - which a caller would hear
# as the agent changing person mid-sentence.
#
# BUMP THIS WHENEVER THE REFERENCE CLIP CHANGES. It is the only thing that
# invalidates cached audio, because the reference lives in the endpoint's
# model.tar.gz and this process cannot see it to hash it.
#
# This has already been missed once, and it is worth understanding how, because the
# failure is silent and looks like the fix simply not working. The value was
# "omnivoice-kajal-ref-v1" - Kajal being the POLLY voice the old reference was
# synthesised from. When the reference was replaced with a human recording, this
# string stayed put, so all 85 shared prompts kept resolving to audio cloned from
# Polly. The endpoint had the new voice and no caller would ever have heard it: the
# cache answers first, and it answers with whatever it stored.
#
# The reference set is MIXED, and the version string says so rather than implying a
# single voice:
#   en        human clip, 6.4s, 222Hz, peak-normalised
#   hi        Polly Kajal neural hi-IN  (build-time only; Polly never serves a call)
#   hinglish  Polly Kajal neural hi-IN
# Replace either Polly reference with a human clip from the same speaker and bump to
# v3. Nothing has been cached under v2 yet, so redefining it here is safe; once audio
# exists under a string, that string is spent.
VOICE_VERSION = os.environ.get("PROMPT_CACHE_VOICE", "omnivoice-mixed-ref-v2")


# --------------------------------------------------------------------------- #
# segmentation
#
# LIVES HERE, NOT IN media/tts.py, because it is part of the cache's ADDRESSING.
# The runtime splits utterances into sentence segments and looks each one up;
# tools/build_prompt_cache.py must therefore seed exactly those segments. Two
# implementations of this split would be two ways to compute the key, and this
# cache's history already contains four bugs of the builder-and-consumer-disagree
# class. Both sides import this one function.
# --------------------------------------------------------------------------- #
_SENTENCE_SPLIT = re.compile(r"(?<=[।?!.])\s+")

# Below this a fragment merges into its neighbour rather than standing alone:
# zero-shot TTS on a one-character piece produces artefacts, and a cache entry
# that small saves nothing worth having.
#
# 4, NOT 8, AND THE DIFFERENCE WAS A LIVE CALL STILL FULL OF DEAD AIR. The most
# common Hindi acknowledgements are SHORT: "ठीक है." is 7 characters, "ज़रूर।" is
# 6. At 8 they merged INTO the question they preceded, recreating the exact
# unique-prefix problem the split exists to solve - the log showed
# "segment cache miss: 'ठीक है. क्या यह अभी नियंत्रण में है?'" on turn after
# turn while the standalone ack sat pre-seeded in the cache, unreachable. The
# acks are proof short segments synthesise fine: they were built standalone by
# tools/build_prompt_cache.py from the ACKNOWLEDGEMENTS list.
MIN_SEGMENT_CHARS = 4


def split_segments(text: str) -> list[str]:
    """Split an utterance at sentence boundaries into cacheable segments.

    Sentence boundaries because a sentence is the unit a voice can synthesise
    with natural prosody in isolation. The regex requires whitespace AFTER the
    terminator, which keeps "user@example.com" and "AS-2026-0098421" whole -
    their dots have no space following them.
    """
    parts = [p.strip() for p in _SENTENCE_SPLIT.split((text or "").strip())
             if p.strip()]
    if not parts:
        return []
    merged: list[str] = []
    for p in parts:
        if merged and (len(merged[-1]) < MIN_SEGMENT_CHARS
                       or len(p) < MIN_SEGMENT_CHARS):
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return merged


_DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine")

# An identifier needs at least this many digits before it is read out character by
# character. THE THRESHOLD IS THE WHOLE SAFETY PROPERTY, because the transform is
# actively WRONG on ordinary quantities: a weight of "55" must stay "fifty-five", a
# dose of "500" must stay "five hundred", and a year of "2026" must stay "two
# thousand twenty-six". Every quantity this system says aloud - ages, doses,
# heights, weights, years - is four digits or fewer, and every identifier it says
# aloud is longer, so five is the line.
#
# It also protects the names that merely contain digits. COVID-19, H1N1, B12, D3
# and type-2 all sit below it and are left exactly as written, which they must be:
# "C O V I D, one nine" would be worse than the problem being fixed.
_ID_MIN_DIGITS = 5
_ID_SEPARATORS = "-/_."

# EMAIL ADDRESSES GET NO DIGIT THRESHOLD, because the digits are not the problem -
# "@" and "." are. The closing item (seed A990) reads the address on file aloud and
# asks the caller to confirm it, which makes it the one utterance in the call where
# they must catch a string of characters exactly.
#
# Measured the same way as the policy number, in the real Hindi carrier sentence:
#
#   priya.sharma@gmail.com  verbatim   -> "मैं प्रिया तोकम पर सारांश..."   destroyed
#                           at/dot     -> "priya.sharma at gmail.com"    exact
#
# SPELLING IT OUT IS WORSE, which was the intuitive fix and is why this was
# measured rather than reasoned. Letter-by-letter gave "pya.ia.gmail.com", and
# spelling the domain too gave "PRIYASHCOM" - the model runs spelled letters
# together. Only the separators need saying; the words themselves are fine.
_EMAIL_AT = " at "
_EMAIL_DOT = " dot "


def speakable_identifiers(text: str) -> str:
    """Rewrite policy numbers and similar IDs into something a listener can follow.

    MEASURED, not styled. The opening line names the caller's policy, and sent to
    OmniVoice verbatim the number came out as mush - a caller reported the policy
    number as the one part of the call they could not understand. Synthesising the
    real sentence and reading it back with Whisper reproduced it exactly:

      "...बीमा पॉलिसी AS-2026-3986649 के बारे में"
        heard as "...बीमा पॉलिसी एस दोचे तीनो एंट से सेच वाटनी के बारे में"
        0 of 11 digits recoverable

    The same sentence with the number expanded to English digit words round-tripped
    perfectly - "...बीमा Policy AS20263986649 के बारे में", 11 of 11 - so the audio
    is genuinely intelligible rather than merely different.

    Two renderings that look just as reasonable measured WORSE, which is why this
    one is not a matter of taste:
      * Hindi digit words ("दो शून्य दो छह") recovered 8 of 11 - and this is a Hindi
        sentence, so that was the intuitive choice.
      * A comma after every single digit collapsed entirely, 0 of 11, coming back as
        "It's do, me, or Jah know." Punctuation is not a pause to this model; it
        reads it as content.

    Applied at SYNTHESIS rather than in the orchestrator's wording, because the
    orchestrator's text is what gets logged, archived, written to Salesforce and
    shown in the transcript - and "A S, two zero two six, three nine..." sitting in
    a policy field on a medical record would be unreadable. This is a property of
    the speech, so it belongs to the thing making speech.

    Shared with tools/build_prompt_cache.py so both synthesis paths pronounce an
    utterance the same way. It runs AFTER cache_key, so keys stay content-addressed
    on the original text and nothing here can orphan the cache.
    """
    if not text:
        return text

    def expand_email(token: str) -> str:
        local, _, domain = token.partition("@")
        return (local.replace(".", _EMAIL_DOT) + _EMAIL_AT
                + domain.replace(".", _EMAIL_DOT))

    def expand(token: str) -> str:
        # Split on the separators an ID uses, and again at each letter/digit
        # boundary, so every run becomes its own phrase. That gives the model a
        # natural boundary without a comma per digit, which was measured to be the
        # one thing that destroys it completely.
        groups: list[str] = []
        current = ""
        kind: str | None = None
        for ch in token:
            if ch in _ID_SEPARATORS:
                if current:
                    groups.append(current)
                current, kind = "", None
                continue
            this_kind = "digit" if ch in "0123456789" else "alpha"
            if kind and this_kind != kind and current:
                groups.append(current)
                current = ""
            current += ch
            kind = this_kind
        if current:
            groups.append(current)

        spoken = []
        for group in groups:
            if group[0] in "0123456789":
                spoken.append(" ".join(_DIGIT_WORDS[int(c)] for c in group))
            else:
                # Letters are spelled out. "AS" is a prefix, not a word.
                spoken.append(" ".join(group.upper()))
        return ", ".join(p for p in spoken if p)

    out = []
    for token in text.split(" "):
        # Punctuation around the token is preserved, so a sentence-final identifier
        # keeps its full stop and the sentence still lands.
        lead, trail, core = "", "", token
        while core and not core[0].isalnum():
            lead += core[0]
            core = core[1:]
        while core and not core[-1].isalnum():
            trail = core[-1] + trail
            core = core[:-1]

        # Addresses first, and with no digit threshold - see _EMAIL_AT. A "@" with a
        # dotted domain after it is an address and nothing else in this call is.
        if "@" in core and "." in core.partition("@")[2]:
            out.append(lead + expand_email(core) + trail)
        elif sum(1 for c in core if c in "0123456789") >= _ID_MIN_DIGITS:
            out.append(lead + expand(core) + trail)
        else:
            out.append(token)
    return " ".join(out)


def cache_key(text: str, language: str) -> str:
    """Content-addressed, so edited text can never serve stale audio."""
    norm = " ".join((text or "").split())
    digest = hashlib.sha256(
        f"{VOICE_VERSION}|{language}|{norm}".encode()).hexdigest()
    return digest[:32]


def s3_key(key: str) -> str:
    # Two-character shard so a bucket listing stays navigable at thousands of
    # entries rather than being one flat directory.
    return f"{PREFIX}/{key[:2]}/{key}.wav"


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> bytes:
    """Strip the container. Stored as WAV rather than raw PCM on purpose: a
    cache entry you can double-click and listen to is worth 44 bytes of header
    when the question is 'why did the agent say that?'."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getframerate() != SAMPLE_RATE:
            log.warning("cached audio is %dHz, expected %d",
                        w.getframerate(), SAMPLE_RATE)
        return w.readframes(w.getnframes())


class PromptCache:
    """Local disk in front of S3, with an in-process memo in front of that.

    Three tiers because they fail differently: the memo avoids repeated disk reads
    within a call, disk survives a service restart without re-fetching 85 objects,
    and S3 is the durable shared copy that a freshly deployed box inherits.
    """

    def __init__(self, bucket: str | None = None, local_dir: str | None = None):
        self._bucket = bucket if bucket is not None else BUCKET
        self._dir = local_dir or LOCAL_DIR
        self._memo: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._s3 = None
        self._disk_warned = False
        self.stats = {"memo": 0, "disk": 0, "s3": 0, "miss": 0}

        # PROBE BY ACTUALLY WRITING, not by ensuring the directory exists.
        #
        # This used to be a bare makedirs(exist_ok=True), which is not a
        # writability test at all: if the directory is already there, makedirs
        # returns success without attempting a single write. Under
        # ProtectSystem=strict the directory existed and was read-only, so the
        # probe passed, self._dir stayed set, and every subsequent write failed
        # with EROFS into a log.debug the service never emitted at INFO.
        #
        # The result was a cache that reported "cached N bytes for next time" and
        # persisted nothing, missing on every call and paying ~2.9s of GPU
        # synthesis per utterance. On a phone line that is heard as silence, and it
        # was blamed on the model rather than on a mount option.
        #
        # Failing loudly here, once, at startup, is the difference between a
        # ten-second fix and a week of chasing latency.
        try:
            os.makedirs(self._dir, exist_ok=True)
            probe = os.path.join(self._dir, ".writable")
            with open(probe, "wb") as fh:
                fh.write(b"1")
            os.unlink(probe)
        except OSError as e:
            # Not fatal. Without a writable directory the cache still works from
            # S3 and the memo; it just re-fetches after a restart. But it is a
            # real degradation and must be visible, so this is WARNING not DEBUG.
            log.warning("prompt cache dir %s is NOT WRITABLE (%s); running without "
                        "the disk tier. Every prompt will be re-fetched from S3 "
                        "after a restart, and any prompt not in S3 will be "
                        "synthesised live at ~2.9s of dead air. If this is EROFS, "
                        "the systemd sandbox needs %s in ReadWritePaths.",
                        self._dir, e, self._dir)
            self._dir = ""

    def _persist(self, key: str, data: bytes) -> bool:
        """Write one entry to the disk tier. Returns whether it landed.

        One implementation for all three call sites. It was three copies, each
        swallowing OSError into a log.debug, and that duplication is why a
        completely dead disk tier went unnoticed: no single place was responsible
        for noticing, so nowhere reported it.

        Warns ONCE and then stays quiet. A cache write happens per utterance, so
        an unconditional warning would repeat all call; but silence is what let
        this hide, and DEBUG on a service running at INFO is silence.
        """
        if not self._dir:
            return False
        tmp = self._disk_path(key) + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            # Atomic rename, so a crash mid-write cannot leave a truncated entry
            # that then plays as a clipped question.
            os.replace(tmp, self._disk_path(key))
            return True
        except OSError as e:
            if not self._disk_warned:
                self._disk_warned = True
                log.warning("prompt cache cannot persist to %s (%s). Continuing "
                            "without the disk tier; this costs an S3 fetch per "
                            "prompt after each restart. Logged once per process.",
                            self._dir, e)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False

    def _client(self):
        if self._s3 is None:
            import boto3
            self._s3 = boto3.client("s3", region_name=DATA_REGION)
        return self._s3

    def _disk_path(self, key: str) -> str:
        return os.path.join(self._dir, f"{key}.wav")

    def get(self, text: str, language: str) -> bytes | None:
        """Return 8kHz PCM, or None on a miss. Never raises."""
        key = cache_key(text, language)

        with self._lock:
            if key in self._memo:
                self.stats["memo"] += 1
                return self._memo[key]

        if self._dir:
            path = self._disk_path(key)
            if os.path.exists(path):
                try:
                    with open(path, "rb") as fh:
                        pcm = wav_to_pcm(fh.read())
                    with self._lock:
                        self._memo[key] = pcm
                    self.stats["disk"] += 1
                    return pcm
                except Exception as e:  # noqa: BLE001
                    log.warning("corrupt cache entry %s (%s); refetching", path, e)
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

        if not self._bucket:
            self.stats["miss"] += 1
            return None

        try:
            obj = self._client().get_object(Bucket=self._bucket, Key=s3_key(key))
            data = obj["Body"].read()
            pcm = wav_to_pcm(data)
        except Exception as e:  # noqa: BLE001
            # A miss is normal (new form item, edited text), so this is debug
            # rather than a warning - at warning level a legitimately new question
            # would look like a fault on every call until the cache was rebuilt.
            log.debug("prompt cache miss for %s/%s: %s", language, key[:8], e)
            self.stats["miss"] += 1
            return None

        self._persist(key, data)

        with self._lock:
            self._memo[key] = pcm
        self.stats["s3"] += 1
        return pcm

    def put(self, text: str, language: str, pcm: bytes) -> str:
        """Store synthesised audio. Returns the key."""
        key = cache_key(text, language)
        data = pcm_to_wav(pcm)

        if self._bucket:
            try:
                self._client().put_object(
                    Bucket=self._bucket, Key=s3_key(key), Body=data,
                    ContentType="audio/wav",
                    Metadata={"language": language,
                              "voice": VOICE_VERSION,
                              # First 200 chars only: object metadata is capped,
                              # and this is a debugging aid, not the source text.
                              "text": text[:200].encode("ascii", "replace").decode()},
                )
            except Exception as e:  # noqa: BLE001
                log.warning("could not upload prompt cache entry: %s", e)

        self._persist(key, data)

        with self._lock:
            self._memo[key] = pcm
        return key

    def warm_from_s3(self, limit: int = 500) -> int:
        """Pull the whole shared cache to local disk.

        NOT CURRENTLY CALLED FROM ANYWHERE. This used to claim it ran at service
        start, which was untrue, and the difference matters less than it sounds: get()
        writes every S3 hit through to disk, so the cache self-populates as prompts
        are first used. The cost of not warming is one S3 round trip (~50-100ms) the
        first time each prompt is spoken, against a disk read (~0.5ms) thereafter -
        not the ~2.9s live synthesis this cache exists to avoid.

        Wiring it up belongs at PROCESS start, not here and not in
        CachedTTSService.__init__: build_tts() runs per call, so warming from a
        constructor would re-list the whole bucket on every call and make the problem
        worse than the one it solves.
        """
        if not self._bucket or not self._dir:
            return 0
        fetched = 0
        try:
            paginator = self._client().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket,
                                           Prefix=f"{PREFIX}/"):
                for obj in page.get("Contents", []):
                    k = obj["Key"]
                    if not k.endswith(".wav"):
                        continue
                    key = os.path.basename(k)[:-4]
                    if os.path.exists(self._disk_path(key)):
                        continue
                    body = self._client().get_object(
                        Bucket=self._bucket, Key=k)["Body"].read()
                    if not self._persist(key, body):
                        # No point listing the rest of the bucket if the disk tier
                        # is not accepting writes; _persist has already said why.
                        return fetched
                    fetched += 1
                    if fetched >= limit:
                        return fetched
        except Exception as e:  # noqa: BLE001
            log.warning("prompt cache warm failed: %s", e)
        return fetched
