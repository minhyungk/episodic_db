"""Classify waste_type from episode metrics using threshold rules."""

from episodic_db.config import WasteThresholds


def classify_waste_type(
    metrics: dict,
    num_calls: int,
    tool_calls: list[dict],
    thresholds: WasteThresholds | None = None,
) -> str | None:
    """Classify waste type based on metrics and thresholds. Returns None if not wasteful."""
    if thresholds is None:
        thresholds = WasteThresholds()

    has_edit = any(tc["tool_name"] in ("Edit", "Write") for tc in tool_calls)

    input_hashes = [tc["input_hash"] for tc in tool_calls if tc["input_hash"]]
    from collections import Counter
    hash_counts = Counter(input_hashes)
    max_repeat = max(hash_counts.values()) if hash_counts else 0

    if max_repeat >= thresholds.repeated_loop_severe_count:
        return "repeated-loop"
    if max_repeat >= thresholds.repeated_loop_warn_count and num_calls >= thresholds.repeated_loop_window:
        return "repeated-loop"

    error_count = sum(1 for tc in tool_calls if tc.get("is_wasteful"))
    if error_count >= thresholds.expensive_failure_severe:
        return "expensive-failure"

    read_ratio = metrics.get("read_output_token_ratio", 0)
    if num_calls >= thresholds.read_heavy_min_calls and read_ratio >= thresholds.read_heavy_severe_ratio:
        return "read-heavy"
    if num_calls >= thresholds.read_heavy_min_calls and read_ratio >= thresholds.read_heavy_warn_ratio:
        return "read-heavy"

    new_info_rate = metrics.get("new_information_rate", 1.0)
    if (
        num_calls >= thresholds.futile_exploration_min_calls
        and new_info_rate < 0.3
        and not has_edit
    ):
        return "futile-exploration"

    no_new_info_calls = int((1 - new_info_rate) * num_calls)
    if no_new_info_calls >= thresholds.futile_exploration_no_new_info and not has_edit:
        return "futile-exploration"

    if metrics.get("futility_score", 0) > 0.6:
        return "futile-exploration"

    return None
