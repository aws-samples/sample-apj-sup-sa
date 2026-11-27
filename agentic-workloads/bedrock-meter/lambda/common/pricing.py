"""Credits — cost-weighted token totals.

Enforcement compares usage against limits in *credits*, not a raw token sum,
because Bedrock/Anthropic price the four token kinds very differently:

  raw input    : 1x   (baseline — 1 raw input token = 1 credit)
  cache read   : 0.1x  (served from cache — ~10x cheaper)
  cache write  : 1.25x (5-minute TTL write premium)
  output       : 5x    (output : input ratio is 5:1 across Opus/Sonnet/Haiku)

So: credits = raw + cacheRead*0.1 + cacheWrite*1.25 + output*5

Summing the four kinds 1:1 (the old behaviour) overcounts cache-heavy workloads
— Claude Code with prompt caching is ~88% cache read, the cheapest kind. Credits
track actual spend and remove the perverse incentive where using the cache (a
cost optimisation) brought a user closer to their limit.

Weights live in the `default` ConfigTable row so they can be retuned without a
redeploy; these constants are the seed values and the fallback. "credits" is the
user-facing term (console, docs, API); `weighted_total` is the implementation.
"""

RAW_INPUT_WEIGHT = 1.0
CACHE_READ_WEIGHT = 0.1
CACHE_WRITE_WEIGHT = 1.25
OUTPUT_WEIGHT = 5.0

WEIGHT_KEYS = {
    'costWeightCacheRead': CACHE_READ_WEIGHT,
    'costWeightCacheWrite': CACHE_WRITE_WEIGHT,
    'costWeightOutput': OUTPUT_WEIGHT,
}


def weights_from_config(config: dict) -> dict[str, float]:
    """Resolve effective weights from config, falling back to the seed defaults.

    raw input weight is fixed at 1.0 (the baseline unit); only the three priced
    multipliers are configurable.
    """
    return {
        'cache_read': float(config.get('costWeightCacheRead', CACHE_READ_WEIGHT)),
        'cache_write': float(config.get('costWeightCacheWrite', CACHE_WRITE_WEIGHT)),
        'output': float(config.get('costWeightOutput', OUTPUT_WEIGHT)),
    }


def weighted_total(
    raw_input: int,
    cache_read: int,
    cache_write: int,
    output_tokens: int,
    weights: dict[str, float],
) -> int:
    """Cost-weighted token total, rounded to an integer cost unit."""
    total = (
        raw_input * RAW_INPUT_WEIGHT
        + cache_read * weights['cache_read']
        + cache_write * weights['cache_write']
        + output_tokens * weights['output']
    )
    return int(round(total))


def athena_weighted_sum(weights: dict[str, float]) -> str:
    """SQL expression summing weighted cost units over Bedrock invocation rows.

    Weights are floats resolved server-side (never user input), so interpolating
    them into SQL is safe — they cannot carry injection.
    """
    return (
        f"SUM(COALESCE(input.inputtokencount,0) * {RAW_INPUT_WEIGHT}"
        f" + COALESCE(input.cachereadinputtokencount,0) * {weights['cache_read']}"
        f" + COALESCE(input.cachewriteinputtokencount,0) * {weights['cache_write']}"
        f" + COALESCE(output.outputtokencount,0) * {weights['output']})"
    )
