"""Demo: dynamic budget-fit compression for sLLM context injection.

Simulates:
  1. Load conversation from episodic DB
  2. Apply fit_to_budget() — dynamically compresses to fit 32k tokens
  3. Show before/after + what got skeletonized
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from episodic_db.compress import truncate_tool_results, fit_to_budget


def load_session_conversation(log_dir: Path) -> list[dict]:
    """Simulate DB load: use last call file (contains full conversation)."""
    call_files = sorted(log_dir.glob("call_*.json"))
    with open(call_files[-1]) as f:
        data = json.load(f)
    return data["conversation"]["messages"]


def main():
    log_dir = Path("logs/session_swebench-sympy__sympy-20639_d949d04a")
    if not log_dir.exists():
        print(f"ERROR: {log_dir} not found")
        return

    messages = load_session_conversation(log_dir)
    original_chars = len(json.dumps(messages, ensure_ascii=False))
    original_tokens = original_chars // 4

    print("=" * 70)
    print("  Dynamic budget-fit compression demo")
    print("=" * 70)
    print(f"\n  Source: {log_dir.name}")
    print(f"  Messages: {len(messages)}")
    print(f"  Original: {original_chars:,} chars (~{original_tokens:,} tokens)")

    # --- Compare strategies ---
    print(f"\n{'─' * 70}")
    print(f"  {'Strategy':<40} {'Chars':>10} {'~Tokens':>10} {'Ratio':>8} {'Fits':>5}")
    print(f"  {'─'*38:<40} {'─'*8:>10} {'─'*8:>10} {'─'*6:>8} {'─'*3:>5}")

    # Strategy 1: truncate only
    r1 = truncate_tool_results(messages, max_result_chars=100)
    t1 = r1.compressed_chars // 4
    print(f"  {'truncate_tool_results(100)':<40} {r1.compressed_chars:>10,} {t1:>10,} {r1.ratio:>7.1%} {'✓' if t1 < 32000 else '✗':>5}")

    # Strategy 2: fit_to_budget at 32k
    r2 = fit_to_budget(messages, max_tokens=32000)
    t2 = r2.compressed_chars // 4
    print(f"  {'fit_to_budget(32k)':<40} {r2.compressed_chars:>10,} {t2:>10,} {r2.ratio:>7.1%} {'✓' if t2 < 32000 else '✗':>5}")

    # Simulate smaller windows
    for budget in [16000, 8000, 4000]:
        r = fit_to_budget(messages, max_tokens=budget)
        t = r.compressed_chars // 4
        print(f"  {'fit_to_budget(' + str(budget//1000) + 'k)':<40} {r.compressed_chars:>10,} {t:>10,} {r.ratio:>7.1%} {'✓' if t < budget else '✗':>5}")

    # --- Show what fit_to_budget(32k) looks like ---
    print(f"\n{'─' * 70}")
    print(f"  fit_to_budget(32k) breakdown:")
    print(f"    Middle messages skeletonized: {r2.dropped_middle}")
    print(f"    Output messages: {len(r2.compressed_messages)}")

    print(f"\n  Sample of compressed output:")
    print(f"  {'─' * 66}")
    for i, msg in enumerate(r2.compressed_messages[:20]):
        role = msg["role"]
        content = msg.get("content", "")
        if isinstance(content, list):
            texts = []
            for b in content:
                if b.get("type") == "text":
                    texts.append(b["text"][:80])
                elif b.get("type") == "tool_use":
                    texts.append(f'[{b["name"]}]')
                elif b.get("type") == "tool_result":
                    rc = b.get("content", "")
                    texts.append(f'[result:{len(rc) if isinstance(rc, str) else "?"}]')
            preview = " | ".join(texts)[:100]
        else:
            preview = str(content)[:100]
        print(f"    [{i:3d}] {role:>9}: {preview}")

    if len(r2.compressed_messages) > 20:
        print(f"    ... ({len(r2.compressed_messages) - 20} more messages)")


if __name__ == "__main__":
    main()
