"""Shared fixtures for distributed KV store tests."""

from __future__ import annotations

import pytest

from distributed_kv_store.core import RaftCluster, RaftLog, RaftNode, StateMachine
from distributed_kv_store.models import (
    CommandType,
    LogEntry,
    RaftConfig,
)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_CLUSTER_SIZE = 3


@pytest.fixture
def state_machine() -> StateMachine:
    """Create a fresh state machine."""
    return StateMachine()


@pytest.fixture
def raft_log() -> RaftLog:
    """Create a fresh Raft log with sentinel."""
    return RaftLog()


@pytest.fixture
def config() -> RaftConfig:
    """Create a default Raft config."""
    return RaftConfig(cluster_size=DEFAULT_CLUSTER_SIZE)


@pytest.fixture
def single_node(config: RaftConfig) -> RaftNode:
    """Create a single Raft node."""
    return RaftNode("node-0", config)


@pytest.fixture
def cluster(config: RaftConfig) -> RaftCluster:
    """Create a 3-node cluster."""
    return RaftCluster(config)


@pytest.fixture
def elected_cluster(cluster: RaftCluster) -> RaftCluster:
    """Create a cluster with an elected leader."""
    cluster.elect_leader()
    return cluster


@pytest.fixture
def set_entry() -> LogEntry:
    """Create a SET log entry."""
    return LogEntry(
        term=1,
        command_type=CommandType.SET,
        key="test_key",
        value="test_value",
    )


@pytest.fixture
def delete_entry() -> LogEntry:
    """Create a DELETE log entry."""
    return LogEntry(
        term=1,
        command_type=CommandType.DELETE,
        key="test_key",
    )


@pytest.fixture
def batch_set_entry() -> LogEntry:
    """Create a BATCH_SET log entry."""
    return LogEntry(
        term=1,
        command_type=CommandType.BATCH_SET,
        batch_data={"k1": "v1", "k2": "v2", "k3": "v3"},
    )


@pytest.fixture
def noop_entry() -> LogEntry:
    """Create a NOOP log entry."""
    return LogEntry(term=1, command_type=CommandType.NOOP)
