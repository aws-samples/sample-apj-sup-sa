#
# AisleMemory — thin wrapper around Amazon Bedrock AgentCore Memory for the
# Aisle voice agent. Transport-agnostic (no pipecat import) so it can be reused
# by any front end. Verified against bedrock-agentcore 1.3.2.
#
# Two jobs:
#   * Short-term: write each conversation turn as an `event` (durable server-side
#     transcript; also the substrate the service extracts long-term records from).
#   * Long-term: retrieve a single USER_PREFERENCE strategy's records at session
#     start and format them into an ADVISORY system-prompt snippet so a returning
#     shopper is greeted personally.
#
# Scope guardrails (per the build plan): Memory stores conversation TEXT only.
# It never stores the grocery list / cart (a separate DB feature) and is NOT the
# authoritative record for profile/allergens — the DB/tools win on any conflict.
#
# Design rules this file obeys:
#   * No-op cleanly when MEMORY_ID is unset, so the agent always runs.
#   * Every MemoryClient call is synchronous boto3 -> run it off the audio loop
#     via asyncio.to_thread.
#   * Turn-saves are fire-and-forget (asyncio.create_task) and NEVER raise into
#     the pipeline — failures only log.
#

import asyncio
import json
import os
from typing import List, Optional

from loguru import logger

try:
    from bedrock_agentcore.memory import MemoryClient
except Exception:  # pragma: no cover - import guard so the agent still boots
    MemoryClient = None  # type: ignore


# Default namespace template for the USER_PREFERENCE strategy. Set deterministically
# at strategy-creation time (see scripts/setup-memory.sh) precisely so retrieval
# needs no wildcard — retrieve_memories rejects "*". Only {actorId} is substituted.
DEFAULT_PREF_NAMESPACE = "/users/{actorId}/preferences"


class AisleMemory:
    """AgentCore Memory helper. Safe to construct unconditionally; all methods
    are no-ops until a MEMORY_ID is configured."""

    def __init__(self, memory_id: Optional[str] = None, region: Optional[str] = None):
        self.memory_id = memory_id or os.getenv("MEMORY_ID") or ""
        self.region = region or os.getenv("AWS_REGION", "ap-southeast-2")
        self._client = None
        # Resolved USER_PREFERENCE namespace template (may contain {actorId}).
        self._pref_ns_template: Optional[str] = None
        self._ns_lock = asyncio.Lock()

        if self.memory_id and MemoryClient is not None:
            try:
                self._client = MemoryClient(region_name=self.region)
            except Exception as e:  # never let memory setup break the agent
                logger.warning(f"AisleMemory: could not build MemoryClient ({e}); memory disabled")
                self._client = None
        if not self.enabled:
            if not self.memory_id:
                logger.info("AisleMemory: MEMORY_ID unset -> memory disabled (no-op).")
            else:
                logger.warning("AisleMemory: MEMORY_ID set but client unavailable -> disabled.")
        else:
            logger.info(f"AisleMemory: enabled (memory_id={self.memory_id}).")

    @property
    def enabled(self) -> bool:
        """True only when a MEMORY_ID is set AND the client was built."""
        return bool(self.memory_id) and self._client is not None

    # ------------------------------------------------------------------ #
    # Namespace resolution (defensive against the wildcard / placeholder trap)
    # ------------------------------------------------------------------ #
    async def _ensure_pref_namespace(self) -> str:
        """Resolve the USER_PREFERENCE namespace template once and cache it.

        We created the resource with a deterministic ns ({actorId} only). But to
        be robust to a resource provisioned differently, we read the strategy's
        real namespaces via get_memory_strategies and prefer the first one.
        Falls back to DEFAULT_PREF_NAMESPACE on any error."""
        if self._pref_ns_template is not None:
            return self._pref_ns_template
        async with self._ns_lock:
            if self._pref_ns_template is not None:
                return self._pref_ns_template
            template = DEFAULT_PREF_NAMESPACE
            try:
                strategies = await asyncio.to_thread(
                    self._client.get_memory_strategies, self.memory_id
                )
                for s in strategies or []:
                    stype = s.get("type") or s.get("memoryStrategyType") or ""
                    if "userPreference" in stype.lower() or "user_preference" in stype.lower():
                        ns_list = s.get("namespaces") or []
                        if ns_list:
                            template = ns_list[0]
                        break
            except Exception as e:
                logger.warning(f"AisleMemory: namespace resolve failed ({e}); using default.")
            self._pref_ns_template = template
            return template

    # ------------------------------------------------------------------ #
    # Long-term: preferences -> advisory prompt snippet
    # ------------------------------------------------------------------ #
    async def get_preferences_prompt(self, user_id: str, query: str, top_k: int = 5) -> str:
        """Return an ADVISORY system-prompt snippet built from the user's
        long-term preference records, or "" if none/disabled/error."""
        if not self.enabled or not user_id:
            return ""
        try:
            template = await self._ensure_pref_namespace()
            namespace = template.replace("{actorId}", user_id)
            if "{" in namespace:  # an unresolved placeholder would 400; bail safely
                logger.warning(f"AisleMemory: unresolved namespace '{namespace}'; skipping retrieval.")
                return ""
            records = await asyncio.to_thread(
                self._client.retrieve_memories,
                self.memory_id,
                namespace,
                query,
                None,
                top_k,
            )
        except Exception as e:
            logger.warning(f"AisleMemory.get_preferences_prompt failed: {e}")
            return ""

        prefs = _extract_texts(records)
        if not prefs:
            return ""

        bullets = "\n".join(f"- {p}" for p in prefs)
        logger.info(f"AisleMemory: retrieved {len(prefs)} preference record(s) for {user_id}.")
        return (
            "\n\n## Remembered preferences for this returning shopper (ADVISORY)\n"
            "These are soft hints learned from past chats — NOT authoritative. Live tool/DB "
            "results always win on price, availability, allergens, and the cart. Use them to "
            "personalise greetings and suggestions; never assert them as fact or auto-add items.\n"
            f"{bullets}\n"
        )

    # ------------------------------------------------------------------ #
    # Short-term: replay recent turns into a reconnecting session
    # ------------------------------------------------------------------ #
    async def get_recent_turns_messages(
        self, user_id: str, session_id: str, k: int = 6
    ) -> List[dict]:
        """Return the last k turns as [{role, content}] for context rehydration.
        Empty list when disabled / no history / error (a fresh session_id yields
        nothing, which is fine)."""
        if not self.enabled or not user_id or not session_id:
            return []
        try:
            turns = await asyncio.to_thread(
                self._client.get_last_k_turns,
                self.memory_id,
                user_id,
                session_id,
                k,
            )
        except Exception as e:
            logger.warning(f"AisleMemory.get_recent_turns_messages failed: {e}")
            return []

        messages: List[dict] = []
        for turn in turns or []:
            for msg in turn or []:
                role = (msg.get("role") or "").lower()
                text = _content_text(msg.get("content"))
                if role in ("user", "assistant") and text:
                    messages.append({"role": role, "content": text})
        if messages:
            logger.info(f"AisleMemory: rehydrated {len(messages)} message(s) for session {session_id}.")
        return messages

    # ------------------------------------------------------------------ #
    # Short-term: persist one user+assistant exchange (fire-and-forget)
    # ------------------------------------------------------------------ #
    def save_turn_bg(
        self,
        user_id: str,
        session_id: str,
        user_text: str,
        assistant_text: str,
    ) -> Optional[asyncio.Task]:
        """Schedule a create_event for this turn without blocking the pipeline.
        Returns the asyncio.Task (so a disconnect handler can briefly await the
        last one) or None when there's nothing to save / memory is disabled."""
        if not self.enabled or not user_id or not session_id:
            return None
        messages = []
        if user_text and user_text.strip():
            messages.append((user_text.strip(), "USER"))
        if assistant_text and assistant_text.strip():
            messages.append((assistant_text.strip(), "ASSISTANT"))
        if not messages:
            return None
        return asyncio.create_task(
            self._save_turn(user_id, session_id, messages)
        )

    async def _save_turn(self, user_id: str, session_id: str, messages: list) -> None:
        try:
            await asyncio.to_thread(
                self._client.create_event,
                self.memory_id,
                user_id,
                session_id,
                messages,
            )
            logger.debug(
                f"AisleMemory: saved turn ({len(messages)} msg) for {user_id}/{session_id}."
            )
        except Exception as e:
            # Never propagate into the audio pipeline; just log.
            logger.warning(f"AisleMemory.save_turn failed (non-fatal): {e}")


# ---------------------------------------------------------------------- #
# Module-level helpers for the varied AgentCore record/content shapes.
# ---------------------------------------------------------------------- #
def _content_text(content) -> str:
    """Pull plain text out of an AgentCore content field, which may be
    {"text": "..."}, a bare string, or a list of such blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        return str(content.get("text", "")).strip()
    if isinstance(content, list):
        parts = [_content_text(c) for c in content]
        return " ".join(p for p in parts if p).strip()
    return str(content).strip()


def _extract_texts(records) -> List[str]:
    """Extract human-readable preference lines from retrieve_memories'
    memoryRecordSummaries.

    The USER_PREFERENCE extractor stores each record's text as a JSON object like
    {"context": "<English summary>", "preference": "<may be localised>",
     "categories": [...]}. We prefer the English `context`, fall back to
     `preference`, then the raw text — so the prompt snippet stays clean English
     regardless of the STT language the preference was first heard in."""
    out: List[str] = []
    for r in records or []:
        raw = ""
        if isinstance(r, dict):
            raw = _content_text(r.get("content")) or _content_text(r.get("text"))
        else:
            raw = _content_text(r)
        line = _readable_pref(raw)
        if line and line not in out:
            out.append(line)
    return out


def _readable_pref(raw: str) -> str:
    """Turn one preference record's stored text into a clean English line."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if isinstance(obj, dict):
            context = str(obj.get("context", "")).strip()
            preference = str(obj.get("preference", "")).strip()
            # Prefer the English context summary; append the (possibly localised)
            # preference only if it adds ascii-readable signal beyond the context.
            if context:
                return context
            if preference:
                return preference
            return raw
    return raw
