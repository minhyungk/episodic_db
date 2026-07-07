"""Per-model cost calculation."""

PRICING = {
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-7": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}


def _match_model(model: str) -> dict:
    for prefix, prices in PRICING.items():
        if model.startswith(prefix):
            return prices
    return PRICING["claude-sonnet-4-6"]


def calculate_cost(model: str, tokens: dict) -> dict:
    prices = _match_model(model)
    input_cost = (tokens.get("input_tokens", 0) / 1_000_000) * prices["input"]
    output_cost = (tokens.get("output_tokens", 0) / 1_000_000) * prices["output"]
    cache_write_cost = (tokens.get("cache_creation_input_tokens", 0) / 1_000_000) * prices["cache_write"]
    cache_read_cost = (tokens.get("cache_read_input_tokens", 0) / 1_000_000) * prices["cache_read"]
    return {
        "input_cost": round(input_cost, 8),
        "output_cost": round(output_cost, 8),
        "cache_write_cost": round(cache_write_cost, 8),
        "cache_read_cost": round(cache_read_cost, 8),
        "total_cost": round(input_cost + output_cost + cache_write_cost + cache_read_cost, 8),
    }
