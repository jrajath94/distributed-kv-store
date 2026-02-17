"""Performance benchmarks for the distributed KV store.

Measures throughput and latency for core operations:
- Single key writes
- Batch writes
- Key lookups
- Election time
"""

from __future__ import annotations

import logging
import statistics
import time

from distributed_kv_store.core import RaftCluster, StateMachine
from distributed_kv_store.models import CommandType, LogEntry, RaftConfig

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

BENCHMARK_SEPARATOR = "=" * 60


def bench_state_machine_apply(iterations: int = 10_000) -> dict[str, float]:
    """Benchmark raw state machine apply throughput.

    Args:
        iterations: Number of apply operations.

    Returns:
        Benchmark results.
    """
    sm = StateMachine()
    entries = [
        LogEntry(
            command_type=CommandType.SET,
            key=f"key_{i}",
            value=f"value_{i}",
        )
        for i in range(iterations)
    ]

    start = time.perf_counter()
    for entry in entries:
        sm.apply(entry)
    elapsed = time.perf_counter() - start

    ops_per_sec = iterations / elapsed
    return {
        "operations": iterations,
        "elapsed_s": elapsed,
        "ops_per_sec": ops_per_sec,
        "us_per_op": (elapsed / iterations) * 1_000_000,
    }


def bench_cluster_writes(
    cluster_size: int = 3, num_writes: int = 1000
) -> dict[str, float]:
    """Benchmark cluster write throughput.

    Args:
        cluster_size: Number of nodes.
        num_writes: Number of write operations.

    Returns:
        Benchmark results.
    """
    config = RaftConfig(cluster_size=cluster_size)
    cluster = RaftCluster(config)
    cluster.elect_leader()

    latencies: list[float] = []

    start = time.perf_counter()
    for i in range(num_writes):
        t0 = time.perf_counter()
        cluster.put(f"key_{i}", f"value_{i}")
        latencies.append(time.perf_counter() - t0)
    elapsed = time.perf_counter() - start

    latencies_us = [lat * 1_000_000 for lat in latencies]
    latencies_us.sort()

    return {
        "operations": num_writes,
        "cluster_size": cluster_size,
        "elapsed_s": elapsed,
        "ops_per_sec": num_writes / elapsed,
        "p50_us": latencies_us[len(latencies_us) // 2],
        "p99_us": latencies_us[int(len(latencies_us) * 0.99)],
        "mean_us": statistics.mean(latencies_us),
    }


def bench_cluster_reads(
    cluster_size: int = 3, num_reads: int = 5000
) -> dict[str, float]:
    """Benchmark cluster read throughput.

    Args:
        cluster_size: Number of nodes.
        num_reads: Number of read operations.

    Returns:
        Benchmark results.
    """
    config = RaftConfig(cluster_size=cluster_size)
    cluster = RaftCluster(config)
    cluster.elect_leader()

    # Pre-populate
    num_keys = 1000
    for i in range(num_keys):
        cluster.put(f"key_{i}", f"value_{i}")

    latencies: list[float] = []

    start = time.perf_counter()
    for i in range(num_reads):
        t0 = time.perf_counter()
        cluster.get(f"key_{i % num_keys}")
        latencies.append(time.perf_counter() - t0)
    elapsed = time.perf_counter() - start

    latencies_us = [lat * 1_000_000 for lat in latencies]
    latencies_us.sort()

    return {
        "operations": num_reads,
        "cluster_size": cluster_size,
        "elapsed_s": elapsed,
        "ops_per_sec": num_reads / elapsed,
        "p50_us": latencies_us[len(latencies_us) // 2],
        "p99_us": latencies_us[int(len(latencies_us) * 0.99)],
        "mean_us": statistics.mean(latencies_us),
    }


def bench_batch_writes(
    cluster_size: int = 3, batch_size: int = 50, num_batches: int = 100
) -> dict[str, float]:
    """Benchmark batch write throughput.

    Args:
        cluster_size: Number of nodes.
        batch_size: Keys per batch.
        num_batches: Number of batch operations.

    Returns:
        Benchmark results.
    """
    config = RaftConfig(cluster_size=cluster_size)
    cluster = RaftCluster(config)
    cluster.elect_leader()

    batches = [
        {f"batch_{b}_key_{k}": f"value_{b}_{k}" for k in range(batch_size)}
        for b in range(num_batches)
    ]

    latencies: list[float] = []

    start = time.perf_counter()
    for batch in batches:
        t0 = time.perf_counter()
        cluster.batch_put(batch)
        latencies.append(time.perf_counter() - t0)
    elapsed = time.perf_counter() - start

    total_keys = batch_size * num_batches
    latencies_us = [lat * 1_000_000 for lat in latencies]
    latencies_us.sort()

    return {
        "total_keys": total_keys,
        "batch_size": batch_size,
        "num_batches": num_batches,
        "elapsed_s": elapsed,
        "keys_per_sec": total_keys / elapsed,
        "batches_per_sec": num_batches / elapsed,
        "p50_us": latencies_us[len(latencies_us) // 2],
        "p99_us": latencies_us[int(len(latencies_us) * 0.99)],
    }


def bench_election(num_trials: int = 100) -> dict[str, float]:
    """Benchmark election time.

    Args:
        num_trials: Number of elections to run.

    Returns:
        Benchmark results.
    """
    latencies: list[float] = []

    for _ in range(num_trials):
        config = RaftConfig(cluster_size=3)
        cluster = RaftCluster(config)
        t0 = time.perf_counter()
        cluster.elect_leader()
        latencies.append(time.perf_counter() - t0)

    latencies_us = [lat * 1_000_000 for lat in latencies]
    latencies_us.sort()

    return {
        "trials": num_trials,
        "p50_us": latencies_us[len(latencies_us) // 2],
        "p99_us": latencies_us[int(len(latencies_us) * 0.99)],
        "mean_us": statistics.mean(latencies_us),
    }


def bench_scaling() -> list[dict[str, float]]:
    """Benchmark write throughput across cluster sizes.

    Returns:
        List of results per cluster size.
    """
    results = []
    for size in [1, 3, 5, 7]:
        result = bench_cluster_writes(cluster_size=size, num_writes=500)
        results.append(result)
    return results


def print_results(title: str, results: dict[str, float]) -> None:
    """Format and print benchmark results.

    Args:
        title: Benchmark name.
        results: Result metrics.
    """
    print(f"\n{title}")
    print("-" * 40)
    for key, value in results.items():
        if isinstance(value, float):
            print(f"  {key:>20s}: {value:>12.2f}")
        else:
            print(f"  {key:>20s}: {value:>12}")


def main() -> None:
    """Run all benchmarks."""
    print(BENCHMARK_SEPARATOR)
    print("Distributed KV Store -- Benchmark Suite")
    print(BENCHMARK_SEPARATOR)

    # State machine raw throughput
    sm_results = bench_state_machine_apply()
    print_results("State Machine Apply (10k ops)", sm_results)

    # Cluster writes
    write_results = bench_cluster_writes()
    print_results("Cluster Writes (3 nodes, 1k ops)", write_results)

    # Cluster reads
    read_results = bench_cluster_reads()
    print_results("Cluster Reads (3 nodes, 5k ops)", read_results)

    # Batch writes
    batch_results = bench_batch_writes()
    print_results("Batch Writes (50 keys/batch, 100 batches)", batch_results)

    # Election
    election_results = bench_election()
    print_results("Election Time (100 trials)", election_results)

    # Scaling
    print(f"\nCluster Size Scaling")
    print("-" * 60)
    print(f"  {'Nodes':>5s} | {'Ops/sec':>10s} | {'p50 (us)':>10s} | {'p99 (us)':>10s}")
    print(f"  {'-'*5} | {'-'*10} | {'-'*10} | {'-'*10}")
    scaling_results = bench_scaling()
    for r in scaling_results:
        size = int(r["cluster_size"])
        print(
            f"  {size:>5d} | {r['ops_per_sec']:>10.0f} | "
            f"{r['p50_us']:>10.1f} | {r['p99_us']:>10.1f}"
        )

    print(f"\n{BENCHMARK_SEPARATOR}")
    print("Benchmark complete.")
    print(BENCHMARK_SEPARATOR)


if __name__ == "__main__":
    main()
