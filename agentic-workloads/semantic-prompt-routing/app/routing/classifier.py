"""Complexity classifier for cost-optimized model routing.

Uses Amazon Nova Micro to analyse query complexity and produce routing
signals.  A pure-heuristic fallback is provided for when the LLM call
fails or is undesirable (cold-start, cost cap, latency budget).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ComplexitySignals:
    """Routing signals produced by the complexity classifier."""

    complexity_score: float  # 0-1, overall difficulty estimate
    task_type: str  # e.g. "general", "code", "math", "creative", "extraction"
    tools_needed: int  # estimated number of tool calls required (0+)
    context_depth: int  # 0-3: how much prior context matters
    domain_specificity: float  # 0-1: how specialised the domain is
    has_structured_output: bool  # whether the response needs structured format
    language: str  # ISO-639-1 code, e.g. "en", "zh", "ja", "ko"
    token_count: int  # approximate input token count
    reasoning: str  # short explanation of the classification

    # Derived convenience helpers ------------------------------------------------

    @property
    def is_cjk(self) -> bool:
        return self.language in {"zh", "ja", "ko"}

    @property
    def is_code(self) -> bool:
        return self.task_type == "code"

    @property
    def code_score(self) -> float:
        """Proxy score for code-heaviness (used by routing overrides)."""
        return self.complexity_score if self.is_code else 0.0

    @property
    def cjk_score(self) -> float:
        """Proxy score for CJK-heaviness."""
        return self.domain_specificity if self.is_cjk else 0.0


# ---------------------------------------------------------------------------
# Classifier prompt (targeting Nova Micro)
# ---------------------------------------------------------------------------

CLASSIFIER_PROMPT = """\
You are a JSON-only classifier. Your ENTIRE response must be a single valid JSON object.

DO NOT include:
- Explanatory text before the JSON
- Explanatory text after the JSON
- Markdown formatting (no ```)
- Comments
- Multiple JSON objects

Your response must START with { and END with }

Copy this exact structure and fill in the values:
{
  "complexity_score": 0.5,
  "task_type": "general",
  "tools_needed": 0,
  "context_depth": 0,
  "domain_specificity": 0.5,
  "has_structured_output": false,
  "language": "en",
  "reasoning": "Your explanation here"
}

Field requirements:
- complexity_score: number between 0.0 and 1.0 (use 0.15 for simple, 0.35 for easy, 0.55 for medium, 0.85 for hard)
- task_type: MUST be one of these exact strings: "general", "code", "math", "creative", "extraction", "translation", "summarisation", "reasoning"
- tools_needed: integer (0, 1, 2, etc.)
- context_depth: integer (0, 1, 2, or 3)
- domain_specificity: number between 0.0 and 1.0
- has_structured_output: boolean (true or false) - true ONLY if user asks for JSON/CSV/XML/table output
- language: two-letter code ("en", "zh", "ja", "ko", "es", "fr", etc.)
- reasoning: string (keep it brief, under 100 characters)

Remember: Output ONLY valid JSON. No other text."""


# ---------------------------------------------------------------------------
# LLM-based classifier
# ---------------------------------------------------------------------------

_bedrock_client: Optional[boto3.client] = None


def _get_bedrock_client() -> boto3.client:
    """Lazy-initialise a bedrock-runtime client."""
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime")
    return _bedrock_client


def _build_messages(
    query: str,
    history: Optional[list[dict[str, str]]] = None,
) -> list[dict]:
    """Construct the messages payload for Nova Micro."""
    messages: list[dict] = []
    if history:
        for turn in history[-6:]:  # keep last 6 turns to limit tokens
            messages.append(
                {"role": turn.get("role", "user"), "content": [{"text": turn.get("content", "")}]}
            )

    # Ensure first message is always from user
    if messages and messages[0].get("role") != "user":
        # If first message isn't user, skip history and just use the query
        messages = []

    messages.append({"role": "user", "content": [{"text": query}]})
    return messages


def _parse_response(raw: str) -> dict:
    """Extract JSON from the model response, tolerating markdown fences and extra text."""
    if not raw or not raw.strip():
        raise ValueError("Empty response text")

    text = raw.strip()

    # Try 1: Strip ```json ... ``` wrappers if present
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    else:
        # Try 2: Extract first JSON object (handle cases with extra text before/after)
        match = re.search(r'\{.*?\}', text, re.DOTALL)
        if match:
            text = match.group(0)
        else:
            # No JSON found in response
            logger.warning(f"No JSON found in classifier response. Raw text (first 200 chars): {raw[:200]}")
            raise ValueError(f"No JSON object found in response")

    # Clean up potential issues
    text = text.strip()

    # Try parsing as-is first
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Try common JSON repairs
        logger.debug(f"Initial JSON parse failed: {e}. Attempting repairs...")

        # Repair 1: Remove trailing commas before } or ]
        repaired = re.sub(r',(\s*[}\]])', r'\1', text)

        # Repair 2: Add missing commas between fields (common issue)
        # Look for patterns like: "key": value\n  "key2"
        repaired = re.sub(r'(["\d\]}\w])\s*\n\s*"', r'\1,\n  "', repaired)

        # Repair 3: Fix unquoted keys
        repaired = re.sub(r'(\w+)(\s*:\s*)', r'"\1"\2', repaired)

        if repaired != text:
            try:
                logger.debug("JSON repair successful")
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

        # If repairs didn't work, log and re-raise
        logger.warning(f"Failed to parse JSON: {e}. Text (first 300 chars): {text[:300]}")
        raise


async def classify_query(
    query: str,
    history: Optional[list[dict[str, str]]] = None,
) -> ComplexitySignals:
    """Classify *query* complexity via Amazon Nova Micro.

    Falls back to :func:`classify_query_heuristic` on any error so that
    routing is never blocked by a classifier failure.
    """
    try:
        client = _get_bedrock_client()
        body = json.dumps(
            {
                "messages": _build_messages(query, history),
                "system": [{"text": CLASSIFIER_PROMPT}],
                "inferenceConfig": {
                    "maxTokens": 300,
                    "temperature": 0.0,
                    "topP": 0.9,
                },
            }
        )
        response = client.invoke_model(
            modelId="amazon.nova-micro-v1:0",
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        result = json.loads(response["body"].read())
        output_text: str = result["output"]["message"]["content"][0]["text"]

        # Log raw response if it looks suspicious
        if not output_text or not output_text.strip():
            logger.warning("Empty response from classifier model")
            raise ValueError("Empty response from classifier")

        parsed = _parse_response(output_text)

        # Validate required fields are present
        required_fields = ["complexity_score"]
        missing_fields = [f for f in required_fields if f not in parsed]
        if missing_fields:
            logger.warning(
                f"Classifier response missing required fields: {missing_fields}. "
                f"Got fields: {list(parsed.keys())}. Response: {output_text[:200]}"
            )
            raise KeyError(f"Missing required fields: {missing_fields}")

        token_count = _estimate_tokens(query, history)

        return ComplexitySignals(
            complexity_score=float(parsed["complexity_score"]),
            task_type=str(parsed.get("task_type", "general")),
            tools_needed=int(parsed.get("tools_needed", 0)),
            context_depth=int(parsed.get("context_depth", 0)),
            domain_specificity=float(parsed.get("domain_specificity", 0.0)),
            has_structured_output=bool(parsed.get("has_structured_output", False)),
            language=str(parsed.get("language", "en")),
            token_count=token_count,
            reasoning=str(parsed.get("reasoning", "")),
        )

    except (ClientError, json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("LLM classifier failed (%s), falling back to heuristic", exc)
        return classify_query_heuristic(query, history)
    except Exception as exc:
        logger.error("Unexpected classifier error: %s", exc, exc_info=True)
        return classify_query_heuristic(query, history)


async def classify_query_ollama(
    query: str,
    history: Optional[list[dict[str, str]]] = None,
    model: str = "llama3.2",
    ollama_url: str = "http://localhost:11434",
) -> ComplexitySignals:
    """Classify query complexity via local Ollama instance.

    Args:
        query: User query to classify
        history: Optional conversation history
        model: Ollama model to use (default: llama3.2)
        ollama_url: Ollama API base URL (default: http://localhost:11434)

    Returns:
        ComplexitySignals with classification results

    Falls back to heuristic classification on any error.
    """
    try:
        import aiohttp

        # Build the classification request as a single user message
        # Embed the query as data within the classification instruction
        classification_request = f"""You are a query-complexity classifier. Analyse this user query and return ONLY a JSON object.

USER QUERY TO CLASSIFY:
\"\"\"
{query}
\"\"\"

CONVERSATION HISTORY (if relevant):
{json.dumps([{"role": t.get("role", "user"), "content": t["content"]} for t in (history[-6:] if history else [])], indent=2)}

Return ONLY this JSON structure with no other text, markdown, or explanation:
{{
  "complexity_score": <float 0-1>,
  "task_type": "<general|code|math|creative|extraction|translation|summarisation|reasoning>",
  "tools_needed": <int 0+>,
  "context_depth": <int 0-3>,
  "domain_specificity": <float 0-1>,
  "has_structured_output": <bool>,
  "language": "<ISO-639-1 of the QUERY>",
  "reasoning": "<1-2 sentence explanation>"
}}

Scoring guide:
- complexity_score: 0.0-0.2=trivial, 0.2-0.4=easy, 0.4-0.7=medium, 0.7-1.0=hard
- has_structured_output: true if query asks for specific format like JSON/CSV/table

RESPOND ONLY WITH THE JSON OBJECT - DO NOT answer the user's query, DO NOT include any other text."""

        # Single message without system prompt for better Ollama compatibility
        messages = [{"role": "user", "content": classification_request}]

        # Call Ollama API with format: "json" to force JSON output
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "format": "json",  # Force JSON mode
                    "options": {
                        "temperature": 0.0,
                        "num_predict": 500,
                    },
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise Exception(f"Ollama API error: {response.status}")

                result = await response.json()
                message = result.get("message", {})

                # Try content first, then thinking field (for models like gemma4 that separate reasoning)
                output_text = message.get("content", "")

                # If content is empty but thinking exists, try to extract JSON from thinking
                if not output_text and "thinking" in message:
                    thinking_text = message.get("thinking", "")
                    logger.debug(f"Content empty, checking thinking field: {thinking_text[:200]}")
                    # Try to find JSON in thinking text
                    try:
                        # Look for JSON object in thinking
                        import re
                        json_match = re.search(r'\{[^{}]*"complexity_score"[^{}]*\}', thinking_text, re.DOTALL)
                        if json_match:
                            output_text = json_match.group(0)
                            logger.debug(f"Extracted JSON from thinking: {output_text[:200]}")
                    except Exception as e:
                        logger.debug(f"Could not extract JSON from thinking: {e}")

                if not output_text:
                    logger.error(f"Empty response from Ollama. Full result: {result}")
                    raise ValueError("Empty response from Ollama - both content and thinking fields empty or invalid")

                # Log raw response for debugging
                logger.debug(f"Ollama raw response: {output_text[:500]}")

                # Parse JSON response - try to extract JSON from potentially malformed text
                try:
                    parsed = _parse_response(output_text)
                except json.JSONDecodeError as e:
                    # If parsing fails, log the problematic text
                    logger.error(f"Failed to parse Ollama response: {output_text[:200]}")
                    logger.error(f"JSON decode error: {e}")
                    raise

                token_count = _estimate_tokens(query, history)

                return ComplexitySignals(
                    complexity_score=float(parsed["complexity_score"]),
                    task_type=str(parsed.get("task_type", "general")),
                    tools_needed=int(parsed.get("tools_needed", 0)),
                    context_depth=int(parsed.get("context_depth", 0)),
                    domain_specificity=float(parsed.get("domain_specificity", 0.0)),
                    has_structured_output=bool(parsed.get("has_structured_output", False)),
                    language=str(parsed.get("language", "en")),
                    token_count=token_count,
                    reasoning=str(parsed.get("reasoning", "")),
                )

    except ImportError:
        logger.warning("aiohttp not installed. Install with: pip install aiohttp")
        return classify_query_heuristic(query, history)
    except Exception as exc:
        logger.warning("Ollama classifier failed (%s), falling back to heuristic", exc)
        return classify_query_heuristic(query, history)


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------

# Keyword sets for task-type detection
_CODE_KEYWORDS = re.compile(
    r"\b(code|function|implement|debug|refactor|class|def |import |"
    r"algorithm|api|sql|html|css|javascript|python|java|rust|regex)\b",
    re.IGNORECASE,
)
_MATH_KEYWORDS = re.compile(
    r"\b(calculate|equation|integral|derivative|proof|theorem|"
    r"probability|matrix|algebra|statistics|solve)\b",
    re.IGNORECASE,
)
_STRUCTURED_KEYWORDS = re.compile(
    r"\b(json|csv|table|xml|yaml|schema|list of|format as|structured)\b",
    re.IGNORECASE,
)
_REASONING_KEYWORDS = re.compile(
    r"\b(explain why|compare|analyse|analyze|evaluate|trade-?offs?|"
    r"pros and cons|reason|justify|critique)\b",
    re.IGNORECASE,
)

# CJK Unicode ranges
_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff"
    r"\uac00-\ud7af\u1100-\u11ff]"
)


def _estimate_tokens(
    query: str,
    history: Optional[list[dict[str, str]]] = None,
) -> int:
    """Rough token count (~0.75 words per token for English)."""
    text = query
    if history:
        text += " ".join(t.get("content", "") for t in history)
    return max(1, int(len(text.split()) / 0.75))


def _detect_language(text: str) -> str:
    """Simple language detection based on character distribution."""
    cjk_chars = len(_CJK_RE.findall(text))
    total = max(len(text), 1)
    ratio = cjk_chars / total
    if ratio > 0.15:
        # Rough heuristic: Japanese has hiragana/katakana
        if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
            return "ja"
        if re.search(r"[\uac00-\ud7af]", text):
            return "ko"
        return "zh"
    return "en"


def classify_query_heuristic(
    query: str,
    history: Optional[list[dict[str, str]]] = None,
) -> ComplexitySignals:
    """Pure-heuristic complexity classification (no LLM call).

    Uses token count, keyword matching, and character analysis to produce
    reasonable routing signals.  Designed as a fast, zero-cost fallback.
    """
    token_count = _estimate_tokens(query, history)
    language = _detect_language(query)
    query_lower = query.lower()

    # --- Task type detection ---
    is_code = bool(_CODE_KEYWORDS.search(query))
    is_math = bool(_MATH_KEYWORDS.search(query))
    has_structured = bool(_STRUCTURED_KEYWORDS.search(query))
    is_reasoning = bool(_REASONING_KEYWORDS.search(query))

    if is_code:
        task_type = "code"
    elif is_math:
        task_type = "math"
    elif has_structured:
        task_type = "extraction"
    elif is_reasoning:
        task_type = "reasoning"
    else:
        task_type = "general"

    # --- Complexity score ---
    score = 0.15  # baseline

    # Length contribution (longer queries tend to be harder)
    if token_count > 500:
        score += 0.25
    elif token_count > 100:
        score += 0.15
    elif token_count > 30:
        score += 0.05

    # Task-type contribution
    if is_code:
        score += 0.20
    if is_math:
        score += 0.20
    if is_reasoning:
        score += 0.15

    # Multi-part questions
    question_marks = query.count("?")
    if question_marks > 2:
        score += 0.10

    # History depth
    context_depth = 0
    if history:
        context_depth = min(len(history), 3)
        score += context_depth * 0.05

    score = round(min(1.0, max(0.0, score)), 2)

    # --- Domain specificity ---
    domain_specificity = 0.3 if (is_code or is_math) else 0.1
    if language != "en":
        domain_specificity += 0.2

    # --- Tools needed ---
    tools_needed = 0
    if is_code:
        tools_needed = 1
    if has_structured:
        tools_needed += 1

    reasoning = (
        f"Heuristic: {token_count} tokens, task={task_type}, "
        f"lang={language}, depth={context_depth}"
    )

    return ComplexitySignals(
        complexity_score=score,
        task_type=task_type,
        tools_needed=tools_needed,
        context_depth=context_depth,
        domain_specificity=round(min(1.0, domain_specificity), 2),
        has_structured_output=has_structured,
        language=language,
        token_count=token_count,
        reasoning=reasoning,
    )
