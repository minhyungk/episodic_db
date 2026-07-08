"""SWE-bench Lite dataset loader."""

from typing import Optional

from ...base import Benchmark
from .benchmark import SWEBenchBenchmark

DATASET_NAME = "SWE-bench/SWE-bench_Lite"
DATASET_SPLIT = "test"


def discover_swebench_benchmarks(
    limit: Optional[int] = None,
    instance_ids: Optional[list[str]] = None,
) -> list[Benchmark]:
    try:
        from datasets import load_dataset
    except ImportError:
        print("[WARN] 'datasets' package not installed. Run: pip install datasets")
        return []

    dataset = load_dataset(DATASET_NAME, split=DATASET_SPLIT)

    benchmarks: list[Benchmark] = []
    for row in dataset:
        iid = row["instance_id"]
        if instance_ids and iid not in instance_ids:
            continue
        benchmarks.append(SWEBenchBenchmark(instance_id=iid, instance_data=dict(row)))
        if limit and len(benchmarks) >= limit:
            break

    return benchmarks
