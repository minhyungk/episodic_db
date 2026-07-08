"""Main bench runner — start proxy, discover benchmarks, execute, report."""

import argparse
import asyncio
import json
from pathlib import Path

from episodic_db.store.db import Database
from episodic_db.config import Config
from episodic_db.proxy.server import ProxyServer
from episodic_db.proxy.bedrock import BedrockProxyServer
from episodic_db.store.nodes import get_session_tool_calls, get_episodes_by_session
from .executor import BenchmarkExecutor
from .external.swebench import discover_swebench_benchmarks


async def run_bench(args):
    config = Config()
    if args.db:
        config.db_path = Path(args.db)

    log_dir = Path(args.log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    db = Database(config.db_path)
    db.connect()

    # Start proxy
    if args.bedrock:
        proxy = BedrockProxyServer(db=db, port=args.port)
    else:
        proxy = ProxyServer(db=db, port=args.port)
    runner = await proxy.start()
    actual_port = proxy.port
    print(f"Proxy started on http://127.0.0.1:{actual_port} ({'bedrock' if args.bedrock else 'direct'})")

    # Discover benchmarks
    swebench_ids = args.swebench_ids.split(",") if args.swebench_ids else None
    benchmarks = discover_swebench_benchmarks(
        limit=args.limit,
        instance_ids=swebench_ids,
    )

    if not benchmarks:
        print("No benchmarks found. Make sure 'datasets' package is installed.")
        await runner.cleanup()
        db.close()
        return

    print(f"Discovered {len(benchmarks)} benchmark(s):")
    for b in benchmarks:
        print(f"  - {b.name}")
    print()

    # Execute
    executor = BenchmarkExecutor(
        db=db,
        proxy_port=actual_port,
        log_dir=log_dir,
        model=args.model,
        use_bedrock=args.bedrock,
    )
    results = []

    for benchmark in benchmarks:
        print(f"Running: {benchmark.name}...", flush=True)
        result = await executor.run_benchmark(benchmark)
        results.append(result)
        print(f"  Done. Session: {result.session_id}")

        # Report episodic DB stats for this session
        tool_calls = get_session_tool_calls(db.conn, result.session_id)
        episodes = get_episodes_by_session(db.conn, result.session_id)
        print(f"  Tool calls captured: {len(tool_calls)}")
        print(f"  Episodes assembled: {len(episodes)}")
        for ep in episodes:
            print(f"    [{ep['episode_id']}] {ep['waste_type']} | {ep['outcome']} | cost=${ep['total_cost']:.4f}")
        print()

    # Summary
    print("=" * 70)
    print(f"{'Benchmark':<40} {'Calls':<8} {'Episodes':<10} {'Session ID'}")
    print("-" * 70)
    for r in results:
        tool_calls = get_session_tool_calls(db.conn, r.session_id)
        episodes = get_episodes_by_session(db.conn, r.session_id)
        print(f"{r.benchmark_name:<40} {len(tool_calls):<8} {len(episodes):<10} {r.session_id}")
    print("=" * 70)

    # Save report
    report = {
        "results": [
            {
                "session_id": r.session_id,
                "benchmark": r.benchmark_name,
                "tool_calls": len(get_session_tool_calls(db.conn, r.session_id)),
                "episodes": len(get_episodes_by_session(db.conn, r.session_id)),
                "metadata": r.metadata,
            }
            for r in results
        ],
        "db_path": str(config.db_path),
    }
    report_path = log_dir / "bench_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved: {report_path}")
    print(f"Database: {config.db_path}")
    print(f"\nInspect with: episodic-db inspect <session_id>")

    await runner.cleanup()
    db.close()


def main():
    parser = argparse.ArgumentParser(description="Run SWE-bench with Episodic DB capture")
    parser.add_argument("--port", type=int, default=8080, help="Proxy port")
    parser.add_argument("--bedrock", action="store_true", help="Use Bedrock proxy")
    parser.add_argument("--model", type=str, default=None, help="Claude model override")
    parser.add_argument("--log-dir", type=str, default="logs", help="Log output directory")
    parser.add_argument("--db", type=str, default=None, help="Database path")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of benchmarks")
    parser.add_argument("--swebench-ids", type=str, default=None,
                        help="Comma-separated SWE-bench instance IDs")
    args = parser.parse_args()
    asyncio.run(run_bench(args))


if __name__ == "__main__":
    main()
