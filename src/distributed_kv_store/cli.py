"""CLI entry point for the distributed KV store.

Provides a command-line interface for running a Raft cluster
and performing key-value operations interactively.
"""

from __future__ import annotations

import argparse
import logging
import sys

from distributed_kv_store.core import RaftCluster
from distributed_kv_store.models import RaftConfig
from distributed_kv_store.utils import format_cluster_status

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv).

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        prog="kv-store",
        description="Raft-consensus distributed key-value store",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        default=3,
        choices=[1, 3, 5, 7, 9],
        help="Number of nodes in the cluster (must be odd)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run a quick demo of cluster operations",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args(argv)


def run_demo(cluster_size: int) -> None:
    """Run a demonstration of the KV store.

    Args:
        cluster_size: Number of nodes to create.
    """
    config = RaftConfig(cluster_size=cluster_size)
    cluster = RaftCluster(config)

    # Elect a leader
    leader_id = cluster.elect_leader()
    logger.info("Leader elected: %s", leader_id)

    # Single writes
    cluster.put("model_name", "gpt-4")
    cluster.put("learning_rate", "3e-4")
    cluster.put("batch_size", "32")
    logger.info("Wrote 3 keys individually")

    # Batch write
    cluster.batch_put({
        "epoch": "10",
        "loss": "0.042",
        "accuracy": "0.976",
        "optimizer": "adamw",
    })
    logger.info("Batch wrote 4 keys")

    # Reads
    for key in ["model_name", "learning_rate", "loss", "accuracy"]:
        value = cluster.get(key)
        logger.info("  %s = %s", key, value)

    # Delete
    cluster.delete("optimizer")
    logger.info("Deleted 'optimizer'")

    # Status
    status = cluster.status()
    logger.info("Cluster status: %s", format_cluster_status(status))

    # All keys
    all_keys = cluster.keys()
    logger.info("Remaining keys: %s", all_keys)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list.

    Returns:
        Exit code.
    """
    args = parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.demo:
        run_demo(args.nodes)
        return 0

    logger.info("Use --demo to run a cluster demonstration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
