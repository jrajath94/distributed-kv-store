"""Utility functions for the distributed KV store.

Provides timing, serialization, and helper functions used
across the Raft implementation.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from distributed_kv_store.models import ClusterStatus, LogEntry

logger = logging.getLogger(__name__)


def generate_election_timeout(min_ms: int, max_ms: int) -> float:
    """Generate a random election timeout in seconds.

    Raft uses randomized timeouts to prevent split votes.

    Args:
        min_ms: Minimum timeout in milliseconds.
        max_ms: Maximum timeout in milliseconds.

    Returns:
        Timeout in seconds.
    """
    return random.randint(min_ms, max_ms) / 1000.0


def majority_count(cluster_size: int) -> int:
    """Compute the majority threshold for a cluster.

    Args:
        cluster_size: Total number of nodes.

    Returns:
        Minimum number of nodes for a majority.
    """
    return (cluster_size // 2) + 1


def format_cluster_status(status: ClusterStatus) -> str:
    """Format cluster status for logging.

    Args:
        status: Current cluster status.

    Returns:
        Formatted string.
    """
    return (
        f"leader={status.leader_id} | "
        f"term={status.term} | "
        f"nodes={status.num_nodes} | "
        f"committed={status.committed_index} | "
        f"entries={status.total_entries} | "
        f"keys={status.state_machine_size}"
    )


def monotonic_ms() -> int:
    """Get current monotonic time in milliseconds.

    Returns:
        Monotonic timestamp in milliseconds.
    """
    return int(time.monotonic() * 1000)
